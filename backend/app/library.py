"""La carpeta de conocimiento y su indexación en segundo plano.

La carpeta es la fuente de verdad: lo que se copia en ella se indexa, lo que se borra sale
de la base y lo que se mueve o renombra conserva su índice (se reconoce por su hash). Subir un
archivo desde la web es solo otra forma de dejarlo en la carpeta.

Un único hilo procesa la cola en orden, porque transcribir y describir imágenes ocupan la CPU o
la GPU entera; las preguntas al chat siguen respondiéndose mientras tanto.
"""

import hashlib
import logging
import queue
import threading
import time
from pathlib import Path

import numpy as np
import watchfiles
from psycopg.types.json import Jsonb

from . import db, providers
from .config import settings
from .ingest import Piece, embed_title, extract, kind_of

log = logging.getLogger("astrolabio.library")

EMBED_BATCH = 16


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _as_array(v) -> np.ndarray:
    # pgvector >= 0.4 devuelve objetos Vector; las versiones anteriores, arrays de numpy.
    return np.asarray(v.to_numpy() if hasattr(v, "to_numpy") else v, dtype=np.float64)


# ---------- Proyección 2D (mapa de la constelación) ----------

def _projection(vectors: np.ndarray) -> dict:
    mean = vectors.mean(axis=0)
    centered = vectors - mean
    if len(vectors) >= 3:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        comps = vt[:2]
    else:
        rng = np.random.default_rng(7)
        comps = rng.standard_normal((2, vectors.shape[1]))
        comps /= np.linalg.norm(comps, axis=1, keepdims=True)
    pts = centered @ comps.T
    scale = float(np.abs(pts).max()) or 1.0
    return {"mean": mean.tolist(), "components": comps.tolist(), "scale": scale}


def project(vec, proj: dict) -> tuple[float, float]:
    v = np.asarray(vec, dtype=np.float64) - np.asarray(proj["mean"])
    x, y = (np.asarray(proj["components"]) @ v) / proj["scale"]
    return float(np.clip(x, -1.2, 1.2)), float(np.clip(y, -1.2, 1.2))


class Library:
    def __init__(self):
        self.root: Path = settings.knowledge_dir.resolve()
        self.jobs: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.pending: set[tuple[str, str | None]] = set()
        self.progress: dict[str, dict] = {}     # source_id → {"stage", "fraction"}
        self.task: dict | None = None           # tarea global en curso (revectorizar)
        self.projection: dict | None = None
        self.map_version = 0                    # cambia cada vez que se recalcula el mapa
        self._scan_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ----- ciclo de vida -----

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with db.conn() as c:
            # Lo que quedó a medias al cerrar la app vuelve a la cola.
            c.execute("UPDATE sources SET status = 'queued' WHERE status = 'processing'")
            row = c.execute("SELECT value FROM app_settings WHERE key = 'projection'").fetchone()
            self.projection = row["value"] if row else None
        self._check_embed_model(providers.load())
        self.scan()
        if self.projection is None:
            self.enqueue("tidy")
        with db.conn() as c:
            for r in c.execute("SELECT id FROM sources WHERE status = 'queued' ORDER BY created_at").fetchall():
                self.enqueue("ingest", str(r["id"]))
        for target in (self._work, self._watch):
            t = threading.Thread(target=target, daemon=True, name=target.__name__)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()

    def enqueue(self, kind: str, arg: str | None = None):
        if (kind, arg) not in self.pending:
            self.pending.add((kind, arg))
            self.jobs.put((kind, arg))

    # ----- carpeta -----

    def _files(self) -> dict[str, Path]:
        found = {}
        for p in self.root.rglob("*"):
            rel = p.relative_to(self.root)
            # Ocultos y temporales (las subidas se escriben como .nombre.part) se ignoran.
            if p.is_file() and kind_of(p) and not any(part.startswith((".", "~$")) for part in rel.parts):
                found[rel.as_posix()] = p
        return found

    def scan(self) -> None:
        """Reconcilia la carpeta con la base: altas, cambios, bajas y movimientos."""
        queued: list[str] = []
        with self._scan_lock, db.conn() as c:
            files = self._files()
            rows = {r["path"]: r for r in c.execute(
                "SELECT id, path, sha256, size_bytes, mtime_ns FROM sources").fetchall()}
            gone = {path: r for path, r in rows.items() if path not in files}
            gone_by_sha = {r["sha256"]: r for r in gone.values()}
            limit = settings.max_file_mb * 1024 * 1024

            for rel, p in files.items():
                st = p.stat()
                row = rows.get(rel)
                if row and row["size_bytes"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns:
                    continue
                sha = _sha256(p)
                moved = gone_by_sha.pop(sha, None) if not row else None
                if moved:
                    # Mismo contenido con otra ruta: se conserva el índice.
                    gone.pop(moved["path"], None)
                    c.execute("UPDATE sources SET path = %s, name = %s, mtime_ns = %s, updated_at = now() WHERE id = %s",
                              (rel, p.name, st.st_mtime_ns, moved["id"]))
                    continue
                if row and row["sha256"] == sha:
                    c.execute("UPDATE sources SET mtime_ns = %s WHERE id = %s", (st.st_mtime_ns, row["id"]))
                    continue
                too_big = st.st_size > limit
                src = c.execute(
                    """INSERT INTO sources(path, name, kind, sha256, size_bytes, mtime_ns, status, error)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (path) DO UPDATE SET
                         kind = EXCLUDED.kind, sha256 = EXCLUDED.sha256, size_bytes = EXCLUDED.size_bytes,
                         mtime_ns = EXCLUDED.mtime_ns, status = EXCLUDED.status, error = EXCLUDED.error,
                         updated_at = now()
                       RETURNING id""",
                    (rel, p.name, kind_of(p), sha, st.st_size, st.st_mtime_ns,
                     "error" if too_big else "queued",
                     f"El archivo supera {settings.max_file_mb} MB." if too_big else None),
                ).fetchone()
                if not too_big:
                    queued.append(str(src["id"]))

            if gone:
                c.execute("DELETE FROM sources WHERE id = ANY(%s)", ([r["id"] for r in gone.values()],))
        # Se encola después del commit: el hilo de trabajo debe ver las filas ya escritas.
        for source_id in queued:
            self.enqueue("ingest", source_id)
        if gone:
            self.enqueue("tidy")

    def _watch(self):
        # Además de los eventos del sistema de archivos se revisa cada scan_interval segundos:
        # en carpetas montadas en Docker o en red los eventos pueden no llegar.
        while not self._stop.is_set():
            try:
                for _ in watchfiles.watch(self.root, stop_event=self._stop, debounce=1500,
                                          rust_timeout=settings.scan_interval * 1000, yield_on_timeout=True):
                    self.scan()
            except Exception:
                log.exception("Error vigilando la carpeta de conocimiento")
                self._stop.wait(settings.scan_interval)

    # ----- cola -----

    def _work(self):
        while not self._stop.is_set():
            try:
                job = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            self.pending.discard(job)
            kind, arg = job
            try:
                if kind == "ingest":
                    self._ingest(arg)
                elif kind == "reembed":
                    self._reembed()
                if kind in ("ingest", "reembed", "tidy") and self.jobs.empty():
                    self._tidy()
            except Exception:
                log.exception("Error en la tarea %s", job)

    def _ingest(self, source_id: str):
        with db.conn() as c:
            src = c.execute("SELECT id, path, name, kind, sha256 FROM sources WHERE id = %s", (source_id,)).fetchone()
            if not src:
                return
            c.execute("UPDATE sources SET status = 'processing', error = NULL WHERE id = %s", (source_id,))
        path = self.root / src["path"]

        def progress(stage: str, fraction: float | None):
            self.progress[source_id] = {"stage": stage, "fraction": fraction}

        started = time.monotonic()
        try:
            cfg = providers.load()
            pieces, meta = extract(src["kind"], path, src["name"], cfg, progress)
            vectors = self._embed(src["name"], pieces, cfg, progress)
            meta["seconds"] = round(time.monotonic() - started, 1)
            with db.conn() as c, c.transaction():
                current = c.execute("SELECT sha256 FROM sources WHERE id = %s", (source_id,)).fetchone()
                if not current or current["sha256"] != src["sha256"]:
                    return  # el archivo cambió o se borró mientras se procesaba; ya hay otra tarea en cola
                c.execute("DELETE FROM chunks WHERE source_id = %s", (source_id,))
                with c.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO chunks(source_id, idx, modality, page, t_start, t_end, content, asset_id, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        [(source_id, i, p.modality, p.page, p.t_start, p.t_end, p.content, p.asset_id, v)
                         for i, (p, v) in enumerate(zip(pieces, vectors))],
                    )
                c.execute("UPDATE sources SET status = 'ready', meta = %s, chunk_count = %s, updated_at = now() "
                          "WHERE id = %s", (Jsonb(meta), len(pieces), source_id))
            log.info("Indexado %s: %d fragmentos en %.1fs", src["path"], len(pieces), meta["seconds"])
        except (ValueError, providers.ProviderError, OSError) as e:
            self._fail(source_id, str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("Error indexando %s", src["path"])
            self._fail(source_id, f"Error interno: {e}")
        finally:
            self.progress.pop(source_id, None)

    def _fail(self, source_id: str, error: str):
        with db.conn() as c:
            c.execute("UPDATE sources SET status = 'error', error = %s, updated_at = now() WHERE id = %s",
                      (error, source_id))

    def _embed(self, name: str, pieces: list[Piece], cfg: dict, progress) -> list[np.ndarray]:
        items = [(embed_title(name, p.modality, p.locator()), p.content) for p in pieces]
        vectors: list[np.ndarray] = []
        for i in range(0, len(items), EMBED_BATCH):
            progress("Vectorizando", i / len(items))
            vectors += [np.asarray(v, dtype=np.float32) for v in providers.embed_documents(items[i:i + EMBED_BATCH], cfg)]
        return vectors

    # ----- modelo de embeddings -----

    def _check_embed_model(self, cfg: dict) -> None:
        """Toda la base comparte un espacio vectorial: si cambia el modelo, se revectoriza."""
        model = cfg["embed"]["model"]
        with db.conn() as c:
            row = c.execute("SELECT value FROM app_settings WHERE key = 'embed_model'").fetchone()
            if row and row["value"] != model:
                c.execute("UPDATE chunks SET embedding = NULL")
            c.execute("INSERT INTO app_settings(key, value) VALUES ('embed_model', %s) "
                      "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (Jsonb(model),))
            missing = c.execute("SELECT EXISTS (SELECT 1 FROM chunks WHERE embedding IS NULL) AS m").fetchone()["m"]
        if missing:
            self.enqueue("reembed")

    def settings_changed(self, cfg: dict) -> None:
        self._check_embed_model(cfg)

    def reembed_all(self) -> None:
        with db.conn() as c:
            c.execute("UPDATE chunks SET embedding = NULL")
        self.enqueue("reembed")

    def _reembed(self):
        """Recalcula solo los vectores: el texto, las transcripciones y las descripciones se conservan."""
        cfg = providers.load()
        with db.conn() as c:
            rows = c.execute(
                "SELECT c.id, c.modality, c.page, c.t_start, c.t_end, c.content, s.name FROM chunks c "
                "JOIN sources s ON s.id = c.source_id WHERE c.embedding IS NULL ORDER BY c.id"
            ).fetchall()
        try:
            for i in range(0, len(rows), EMBED_BATCH):
                self.task = {"stage": "Revectorizando la base", "done": i, "total": len(rows)}
                batch = rows[i:i + EMBED_BATCH]
                items = [(embed_title(r["name"], r["modality"],
                                      Piece(r["modality"], "", r["page"], r["t_start"], r["t_end"]).locator()),
                          r["content"]) for r in batch]
                vectors = providers.embed_documents(items, cfg)
                with db.conn() as c, c.cursor() as cur:
                    cur.executemany("UPDATE chunks SET embedding = %s WHERE id = %s",
                                    [(np.asarray(v, dtype=np.float32), r["id"]) for v, r in zip(vectors, batch)])
        except providers.ProviderError as e:
            self.task = {"stage": "Revectorizando la base", "error": str(e)}
            return
        self.task = None

    # ----- mantenimiento -----

    def _tidy(self):
        """Borra imágenes huérfanas y recalcula el mapa 2D de toda la base."""
        with db.conn() as c:
            c.execute("DELETE FROM assets a WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.asset_id = a.id)")
            rows = c.execute("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL").fetchall()
            if not rows:
                c.execute("DELETE FROM app_settings WHERE key = 'projection'")
                self.projection = None
                self.map_version += 1
                return
            arr = np.stack([_as_array(r["embedding"]) for r in rows])
            proj = _projection(arr)
            pts = (arr - np.asarray(proj["mean"])) @ np.asarray(proj["components"]).T / proj["scale"]
            pts = np.clip(pts, -1.2, 1.2)
            with c.transaction():
                c.execute(
                    "UPDATE chunks AS c SET x = v.x, y = v.y "
                    "FROM unnest(%s::bigint[], %s::real[], %s::real[]) AS v(id, x, y) WHERE c.id = v.id",
                    ([r["id"] for r in rows], pts[:, 0].tolist(), pts[:, 1].tolist()),
                )
                c.execute("INSERT INTO app_settings(key, value) VALUES ('projection', %s) "
                          "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (Jsonb(proj),))
        self.projection = proj
        self.map_version += 1

    # ----- estado para la UI -----

    def status(self) -> dict:
        return {
            "dir": str(self.root),
            "queued": sum(1 for k, _ in self.pending if k == "ingest"),
            "task": self.task,
            "map_version": self.map_version,
        }


library = Library()
