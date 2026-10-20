import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, providers, rag
from .config import BACKEND, settings
from .ingest import KINDS, kind_of
from .library import library
from .media import whisper_loaded

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("astrolabio")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    library.start()
    yield
    library.stop()
    db.close_db()


app = FastAPI(title="Astrolabio", lifespan=lifespan)


def ndjson(events):
    """Convierte un generador de eventos en un stream NDJSON; los errores viajan como eventos."""
    def gen():
        try:
            for ev in events:
                yield json.dumps(ev, ensure_ascii=False, default=str) + "\n"
        except (ValueError, LookupError, providers.ProviderError) as e:
            yield json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001
            log.exception("Error inesperado")
            yield json.dumps({"type": "error", "error": f"Error interno: {e}"}) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- Estado y configuración ----------

def _check(section: str, cfg: dict) -> dict:
    try:
        models = providers.list_models(section, cfg, timeout=4)
    except providers.ProviderError as e:
        return {"ok": False, "error": str(e)}
    if section == "ollama":
        # Ollama nombra "modelo:latest" cuando no se indica etiqueta.
        have = set(models) | {m.removesuffix(":latest") for m in models}
        if cfg["ollama"]["chat_model"] not in have:
            return {"ok": False, "error": f"Falta descargar: ollama pull {cfg['ollama']['chat_model']}"}
    return {"ok": True}


@app.get("/api/health")
def health():
    cfg = providers.load()
    checks = {s: _check(s, cfg) for s in {cfg["chat_provider"], cfg["vision_provider"], "embed"}}
    return {
        "db": True,
        "limits": {"max_pages": settings.max_pages, "max_file_mb": settings.max_file_mb,
                   "max_media_minutes": settings.max_media_minutes, "extensions": sorted(KINDS)},
        "chat": {"provider": cfg["chat_provider"], "model": providers.model_label(cfg["chat_provider"], cfg),
                 **checks[cfg["chat_provider"]]},
        "vision": {"provider": cfg["vision_provider"], "model": providers.model_label(cfg["vision_provider"], cfg),
                   **checks[cfg["vision_provider"]]},
        "embed": {"model": cfg["embed"]["model"], "dim": settings.embed_dim, **checks["embed"]},
        "whisper": {"model": settings.whisper_model, "loaded": whisper_loaded()},
        "library": library.status(),
    }


@app.get("/api/settings")
def get_settings():
    return providers.public(providers.load())


@app.put("/api/settings")
def put_settings(patch: dict):
    cfg = providers.save(patch)
    library.settings_changed(cfg)
    return providers.public(cfg)


@app.get("/api/models/{section}")
def models(section: str):
    if section not in providers.SECTIONS:
        raise HTTPException(404, "Proveedor desconocido")
    try:
        return {"models": providers.list_models(section)}
    except providers.ProviderError as e:
        raise HTTPException(502, str(e)) from e


# ---------- Base de conocimiento ----------

_SOURCE_COLUMNS = "id, path, name, kind, size_bytes, status, error, meta, chunk_count, created_at, updated_at"


def _serialize(r: dict) -> dict:
    out = {**r, "id": str(r["id"])}
    if r["status"] == "processing":
        out["progress"] = library.progress.get(out["id"])
    return out


@app.get("/api/sources")
def list_sources():
    with db.conn() as c:
        rows = c.execute(f"SELECT {_SOURCE_COLUMNS} FROM sources ORDER BY created_at DESC").fetchall()
    return {"sources": [_serialize(r) for r in rows], "library": library.status()}


def _free_name(name: str) -> Path:
    target = library.root / name
    stem, suffix, n = target.stem, target.suffix, 2
    while target.exists():
        target = library.root / f"{stem} ({n}){suffix}"
        n += 1
    return target


@app.post("/api/sources")
async def upload(files: list[UploadFile] = File(...)):
    """Guarda los archivos en la carpeta de conocimiento; el vigilante se encarga del resto."""
    limit = settings.max_file_mb * 1024 * 1024
    saved, rejected = [], []
    for f in files:
        name = Path(f.filename or "archivo").name
        if not kind_of(Path(name)):
            rejected.append({"name": name, "error": "Formato no admitido."})
            continue
        tmp = library.root / f".{uuid.uuid4().hex}.part"
        size = 0
        try:
            with tmp.open("wb") as out:
                while block := await f.read(1 << 20):
                    size += len(block)
                    if size > limit:
                        raise ValueError(f"Supera {settings.max_file_mb} MB.")
                    out.write(block)
            target = _free_name(name)
            os.replace(tmp, target)
            saved.append(target.relative_to(library.root).as_posix())
        except ValueError as e:
            rejected.append({"name": name, "error": str(e)})
        finally:
            tmp.unlink(missing_ok=True)
    library.scan()
    with db.conn() as c:
        rows = c.execute(f"SELECT {_SOURCE_COLUMNS} FROM sources WHERE path = ANY(%s)", (saved,)).fetchall()
    return {"sources": [_serialize(r) for r in rows], "rejected": rejected}


def _source(source_id: UUID) -> dict:
    with db.conn() as c:
        row = c.execute("SELECT id, path, name, kind FROM sources WHERE id = %s", (source_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Fuente no encontrada")
    return row


@app.delete("/api/sources/{source_id}", status_code=204)
def delete_source(source_id: UUID):
    """Borra el archivo de la carpeta (si no, el vigilante lo volvería a indexar) y su índice."""
    src = _source(source_id)
    try:
        (library.root / src["path"]).unlink(missing_ok=True)
    except PermissionError as e:
        raise HTTPException(409, "El archivo está abierto en otro programa; ciérralo y vuelve a intentarlo.") from e
    library.scan()


@app.post("/api/sources/{source_id}/retry", status_code=202)
def retry_source(source_id: UUID):
    _source(source_id)
    with db.conn() as c:
        c.execute("UPDATE sources SET status = 'queued', error = NULL WHERE id = %s", (source_id,))
    library.enqueue("ingest", str(source_id))


@app.get("/api/sources/{source_id}/file")
def source_file(source_id: UUID):
    """El archivo original, para abrir el PDF en su página o reproducir el audio en su minuto."""
    src = _source(source_id)
    path = (library.root / src["path"]).resolve()
    if library.root not in path.parents or not path.is_file():
        raise HTTPException(404, "El archivo ya no está en la carpeta")
    return FileResponse(path, filename=src["name"], content_disposition_type="inline")


@app.post("/api/library/scan")
def scan():
    library.scan()
    return library.status()


@app.post("/api/library/reembed", status_code=202)
def reembed():
    library.reembed_all()
    return library.status()


@app.get("/api/map")
def knowledge_map():
    with db.conn() as c:
        return c.execute(
            "SELECT c.id, c.source_id::text, c.idx, c.modality, c.page, c.t_start, c.x, c.y, "
            "left(c.content, 220) AS preview, s.name FROM chunks c JOIN sources s ON s.id = c.source_id "
            "WHERE c.embedding IS NOT NULL ORDER BY c.source_id, c.idx LIMIT 5000"
        ).fetchall()


@app.get("/api/assets/{asset_id}")
def asset(asset_id: int):
    with db.conn() as c:
        row = c.execute("SELECT mime, data FROM assets WHERE id = %s", (asset_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Imagen no encontrada")
    # El contenido de un id nunca cambia (se identifica por hash): se puede guardar en caché.
    return Response(bytes(row["data"]), media_type=row["mime"],
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---------- Conversación ----------

@app.get("/api/messages")
def messages():
    with db.conn() as c:
        return c.execute("SELECT id, role, content, sources, created_at FROM messages ORDER BY id").fetchall()


@app.delete("/api/messages", status_code=204)
def clear_messages():
    with db.conn() as c:
        c.execute("DELETE FROM messages")


class Ask(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    scope: list[UUID] = Field(default_factory=list, max_length=50)


@app.post("/api/chat")
def chat(body: Ask):
    return ndjson(rag.answer(body.message.strip(), [str(s) for s in body.scope]))


# ---------- Frontend compilado ----------

def _static_dir() -> Path | None:
    candidates = [Path(settings.static_dir)] if settings.static_dir else [BACKEND / "static", BACKEND.parent / "frontend" / "dist"]
    return next((p for p in candidates if (p / "index.html").is_file()), None)


static = _static_dir()
if static:
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = (static / path).resolve()
        if path and target.is_file() and static.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(static / "index.html")
