"""Indexación (PDF → chunks → embeddings → pgvector) y respuesta con recuperación."""

from collections.abc import Iterator

import numpy as np
from psycopg.types.json import Jsonb

from . import db, providers
from .config import settings
from .pdf import chunk_pages, read_pages

EMBED_BATCH = 16

SYSTEM_PROMPT = """Eres ChatPdf, un asistente que responde preguntas sobre un documento PDF.
Responde SOLO con la información de los fragmentos del documento que se te entregan.
Si la respuesta no está en los fragmentos, dilo con honestidad y sugiere qué preguntar.
Responde en el mismo idioma que la pregunta, de forma clara y concisa, usando Markdown cuando ayude.
Cita las páginas de donde sale cada dato con el formato [p. N]."""


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

def answer(document_id: str, question: str) -> Iterator[dict]:
    with db.conn() as c:
        doc = c.execute(
            "SELECT id, name, embed_provider, embed_model, embed_dim, projection "
            "FROM documents WHERE id = %s",
            (document_id,),
        ).fetchone()
        if not doc:
            raise LookupError("Documento no encontrado")
        history = c.execute(
            "SELECT role, content FROM (SELECT id, role, content FROM messages "
            "WHERE document_id = %s ORDER BY id DESC LIMIT 6) h ORDER BY id",
            (document_id,),
        ).fetchall()

    cfg = providers.load()
    # La pregunta se vectoriza con el mismo modelo con que se indexó el documento.
    qvec = providers.embed([question], doc["embed_provider"], doc["embed_model"], cfg)[0]
    if len(qvec) != doc["embed_dim"]:
        raise providers.ProviderError("El modelo de embeddings cambió de dimensión; vuelve a subir el PDF.")
    q = np.asarray(qvec, dtype=np.float32)

    with db.conn() as c:
        rows = c.execute(
            "SELECT id, idx, page, content, 1 - (embedding <=> %s) AS score "
            "FROM chunks WHERE document_id = %s ORDER BY embedding <=> %s LIMIT %s",
            (q, document_id, q, settings.top_k),
        ).fetchall()

    sources = [
        {"chunk_id": r["id"], "idx": r["idx"], "page": r["page"],
         "score": round(float(r["score"]), 4), "content": r["content"]}
        for r in rows
    ]
    qx, qy = _project(qvec, doc["projection"])
    yield {"type": "sources", "query": {"x": qx, "y": qy}, "sources": sources}

    context = "\n\n".join(f"[Fragmento {i + 1} · p. {s['page']}]\n{s['content']}" for i, s in enumerate(sources))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({
        "role": "user",
        "content": f"Documento: {doc['name']}\n\nFragmentos relevantes:\n{context}\n\nPregunta: {question}",
    })

    parts: list[str] = []
    for piece in providers.chat_stream(messages, cfg):
        parts.append(piece)
        yield {"type": "token", "text": piece}

    reply = "".join(parts).strip()
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
