"""Indexación (PDF → chunks → embeddings → pgvector) y respuesta con recuperación."""

import re
from collections.abc import Iterator

import numpy as np
from psycopg.types.json import Jsonb

from . import db, providers
from .config import settings
from .pdf import chunk_pages, read_pages

EMBED_BATCH = 16

SYSTEM_PROMPT = """Eres ChatPdf, un asistente que explica documentos PDF.
Respondes usando únicamente el texto del documento que viene en el mensaje del usuario.

Cómo responder:
- Explica con claridad y con tus propias palabras. Cuando ayude, organiza la respuesta en puntos o pasos y apóyate en ejemplos o datos del texto.
- Cita la página de donde sale cada dato con el formato [p. N].
- Si te preguntan por una página concreta, explica de qué trata esa página, sus ideas principales y los datos importantes que contiene.
- Si te piden un resumen o de qué trata el documento, recorre las ideas principales en orden.
- Si la respuesta no está en el texto, dilo con claridad y menciona qué temas sí cubre el documento. No inventes información.
- Responde en el mismo idioma de la pregunta."""


# ---------- Proyección 2D (para el mapa de constelación de la UI) ----------

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


def _project(vec, proj: dict) -> tuple[float, float]:
    v = np.asarray(vec, dtype=np.float64) - np.asarray(proj["mean"])
    x, y = (np.asarray(proj["components"]) @ v) / proj["scale"]
    return float(np.clip(x, -1.2, 1.2)), float(np.clip(y, -1.2, 1.2))


# ---------- Indexación ----------

def ingest(name: str, data: bytes) -> Iterator[dict]:
    """Procesa el PDF emitiendo eventos de progreso para la animación de la UI."""
    yield {"stage": "reading"}
    pages = read_pages(data, settings.max_pages)
    yield {"stage": "read", "pages": len(pages)}

    chunks = chunk_pages(pages, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        raise ValueError(
            "No se encontró texto en el PDF. Si es un escaneo, primero aplícale OCR."
        )
    yield {"stage": "chunked", "chunks": len(chunks)}

    cfg = providers.load()
    provider = cfg["embed_provider"]
    model = cfg[provider]["embed_model"]
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i:i + EMBED_BATCH]
        vectors.extend(providers.embed([c.content for c in batch], provider, model, cfg))
        yield {"stage": "embedding", "done": len(vectors), "total": len(chunks)}

    arr = np.asarray(vectors, dtype=np.float64)
    proj = _projection(arr)
    points = [_project(v, proj) for v in arr]

    with db.conn() as c, c.transaction():
        doc = c.execute(
            """INSERT INTO documents(name, pages, size_bytes, chunk_count, embed_provider,
                                     embed_model, embed_dim, projection)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, name, pages, size_bytes, chunk_count, embed_provider,
                         embed_model, created_at""",
            (name, len(pages), len(data), len(chunks), provider, model, arr.shape[1], Jsonb(proj)),
        ).fetchone()
        with c.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks(document_id, idx, page, content, embedding, x, y) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (doc["id"], ch.idx, ch.page, ch.content, np.asarray(v, dtype=np.float32), x, y)
                    for ch, v, (x, y) in zip(chunks, vectors, points)
                ],
            )
    yield {"stage": "done", "document": serialize_doc(doc)}


def serialize_doc(d: dict) -> dict:
    return {
        "id": str(d["id"]),
        "name": d["name"],
        "pages": d["pages"],
        "size_bytes": d["size_bytes"],
        "chunk_count": d["chunk_count"],
        "embed_provider": d["embed_provider"],
        "embed_model": d["embed_model"],
        "created_at": d["created_at"].isoformat(),
    }


# ---------- Chat ----------

# ---------- Recuperación ----------

_ORDINALS = {
    "primera": 1, "primer": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5,
    "sexta": 6, "séptima": 7, "septima": 7, "octava": 8,
}
_PAGE_WORD = r"(?:p[áa]gina|p[áa]g\.?|hoja|page|p\.)"
_GENERAL = re.compile(
    r"\b(resum\w*|de qu[ée] (trata|habla|se trata)|tema (principal|central)|ideas? (principal|clave|central)\w*|"
    r"todo el documento|el documento completo|explica(me)? el documento|summar\w*|overview|what is (it|this) about)\b",
    re.IGNORECASE,
)


def parse_pages(question: str, total: int) -> list[int]:
    """Detecta páginas mencionadas: "página 3", "págs. 2 y 4", "pág 2-3", "tercera página", "última página"."""
    text = question.lower()
    pages: set[int] = set()
    for m in re.finditer(_PAGE_WORD + r"s?\s*(?:n[úu]mero|nro\.?|n°|#)?\s*((?:\d+\s*(?:,|y|e|a|-|–|al|hasta)?\s*)+)", text):
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
        if len(nums) == 2 and re.search(r"\d\s*(-|–|a|al|hasta)\s*\d", m.group(1)):
            pages.update(range(min(nums), max(nums) + 1))
        else:
            pages.update(nums)
    for word, n in _ORDINALS.items():
        if re.search(rf"\b{word}\s+{_PAGE_WORD}", text):
            pages.add(n)
    if re.search(rf"\b(última|ultima)\s+{_PAGE_WORD}|{_PAGE_WORD}\s+final\b", text):
        pages.add(total)
    return sorted(p for p in pages if p > 0)


def _retrieve(document_id: str, question: str, qvec: np.ndarray) -> list[dict]:
    """Todos los fragmentos del documento con su similitud vectorial y por palabras clave."""
    with db.conn() as c:
        rows = c.execute(
            """SELECT id, idx, page, content,
                      1 - (embedding <=> %(q)s) AS score,
                      ts_rank_cd(to_tsvector('spanish', content),
                                 websearch_to_tsquery('spanish', %(text)s)) AS kw
               FROM chunks WHERE document_id = %(doc)s ORDER BY idx""",
            {"q": qvec, "text": question, "doc": document_id},
        ).fetchall()
    # Fusión de rankings (RRF): combina búsqueda semántica y por palabras clave.
    by_vec = sorted(rows, key=lambda r: -r["score"])
    by_kw = [r for r in sorted(rows, key=lambda r: -r["kw"]) if r["kw"] > 0]
    rrf: dict[int, float] = {}
    for rank, r in enumerate(by_vec):
        rrf[r["id"]] = rrf.get(r["id"], 0) + 1 / (60 + rank)
    for rank, r in enumerate(by_kw):
        rrf[r["id"]] = rrf.get(r["id"], 0) + 1 / (60 + rank)
    for r in rows:
        r["rrf"] = rrf[r["id"]]
    return rows


def _select(rows: list[dict], pages: list[int], general: bool, total_pages: int):
    """Decide qué texto ve el modelo. Devuelve (fragmentos para el contexto, nota para el modelo, estado)."""
    ranked = sorted(rows, key=lambda r: -r["rrf"])
    total_chars = sum(len(r["content"]) for r in rows)

    valid = [p for p in pages if p <= total_pages]
    if pages:
        notes = []
        missing = [p for p in pages if p > total_pages]
        if missing:
            notes.append(f"El documento solo tiene {total_pages} páginas; no existe la página {', '.join(map(str, missing))}.")
        chosen = [r for r in rows if r["page"] in valid]
        empty = [p for p in valid if not any(r["page"] == p for r in rows)]
        if empty:
            notes.append(f"La página {', '.join(map(str, empty))} no tiene texto extraíble (puede ser una imagen).")
        extra = [r for r in ranked if r["page"] not in valid][:2]
        label = ", ".join(map(str, valid)) or "—"
        notes.insert(0, f"El usuario pregunta por la página {label}. Abajo está el texto completo de esa página"
                        " y un par de fragmentos relacionados de otras páginas.")
        return chosen + extra, " ".join(notes), f"Leyendo la página {label}"

    if total_chars <= settings.full_doc_chars or general:
        if total_chars <= settings.full_doc_chars:
            return rows, "Abajo está el texto completo del documento.", "Leyendo el documento completo"
        # Documento grande y pregunta general: el inicio de cada página más lo más relevante.
        chosen, used = [], 0
        firsts = [next(r for r in rows if r["page"] == p) for p in sorted({r["page"] for r in rows})]
        for r in firsts + ranked:
            if r in chosen or used + len(r["content"]) > settings.full_doc_chars:
                continue
            chosen.append(r)
            used += len(r["content"])
        return chosen, "Abajo hay una selección representativa de todas las páginas del documento.", "Recorriendo el documento"

    return ranked[: settings.top_k], "Abajo están los fragmentos del documento más relacionados con la pregunta.", "Leyendo los fragmentos más relevantes"


def answer(document_id: str, question: str) -> Iterator[dict]:
    with db.conn() as c:
        doc = c.execute(
            "SELECT id, name, pages, embed_provider, embed_model, embed_dim, projection "
            "FROM documents WHERE id = %s",
            (document_id,),
        ).fetchone()
        if not doc:
            raise LookupError("Documento no encontrado")
        history = c.execute(
            "SELECT role, content FROM (SELECT id, role, content FROM messages "
            "WHERE document_id = %s ORDER BY id DESC LIMIT 4) h ORDER BY id",
            (document_id,),
        ).fetchall()

    yield {"type": "status", "text": "Buscando en el documento"}
    cfg = providers.load()
    # La pregunta se vectoriza con el mismo modelo con que se indexó el documento.
    qvec = providers.embed([question], doc["embed_provider"], doc["embed_model"], cfg)[0]
    if len(qvec) != doc["embed_dim"]:
        raise providers.ProviderError("El modelo de embeddings cambió de dimensión; vuelve a subir el PDF.")

    rows = _retrieve(document_id, question, np.asarray(qvec, dtype=np.float32))
    pages = parse_pages(question, doc["pages"])
    chosen, note, status = _select(rows, pages, bool(_GENERAL.search(question)), doc["pages"])

    # Lo que se dibuja en el mapa: los fragmentos usados, los más relevantes primero.
    on_pages = [r for r in chosen if r["page"] in pages]
    shown = on_pages + sorted((r for r in chosen if r not in on_pages), key=lambda r: -r["rrf"])
    shown = shown[: max(settings.top_k, len(on_pages))]
    sources = [
        {"chunk_id": r["id"], "idx": r["idx"], "page": r["page"],
         "score": round(float(r["score"]), 4), "content": r["content"]}
        for r in shown
    ]
    qx, qy = _project(qvec, doc["projection"])
    yield {"type": "sources", "query": {"x": qx, "y": qy}, "sources": sources}
    yield {"type": "status", "text": status}

    by_page: dict[int, list[str]] = {}
    for r in sorted(chosen, key=lambda r: r["idx"]):
        by_page.setdefault(r["page"], []).append(r["content"])
    context = "\n\n".join(f"[Página {p}]\n" + "\n".join(parts) for p, parts in sorted(by_page.items()))

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        text = m["content"] if m["role"] == "user" else m["content"][:800]
        messages.append({"role": m["role"], "content": text})
    messages.append({
        "role": "user",
        "content": (
            f"Documento: «{doc['name']}» ({doc['pages']} páginas).\n{note}\n\n"
            f"--- TEXTO DEL DOCUMENTO ---\n{context or '(sin texto)'}\n--- FIN DEL TEXTO ---\n\n"
            f"Pregunta: {question}"
        ),
    })

    parts: list[str] = []
    thinking = False
    for kind, piece in providers.chat_stream(messages, cfg):
        if kind == "thinking":
            if not thinking:
                thinking = True
                yield {"type": "status", "text": "Razonando"}
            continue
        if not parts:
            yield {"type": "status", "text": ""}
        parts.append(piece)
        yield {"type": "token", "text": piece}

    reply = "".join(parts).strip()
    if not reply:
        raise providers.ProviderError(
            "El modelo terminó sin escribir una respuesta. Prueba de nuevo o cambia de modelo en el botón motor."
        )
    slim = [{k: s[k] for k in ("chunk_id", "idx", "page", "score", "content")} for s in sources]
    with db.conn() as c:
        c.execute("INSERT INTO messages(document_id, role, content) VALUES (%s, 'user', %s)",
                  (document_id, question))
        row = c.execute(
            "INSERT INTO messages(document_id, role, content, sources) VALUES (%s, 'assistant', %s, %s) "
            "RETURNING id",
            (document_id, reply, Jsonb({"query": {"x": qx, "y": qy}, "items": slim})),
        ).fetchone()
    yield {"type": "done", "message_id": row["id"]}
