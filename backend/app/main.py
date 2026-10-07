import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, providers, rag
from .config import settings
from .pdf import PdfError

log = logging.getLogger("chatpdf")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield
    db.close_db()


app = FastAPI(title="ChatPdf", lifespan=lifespan)


def ndjson(events):
    """Convierte un generador de eventos en un stream NDJSON; los errores viajan como eventos."""
    def gen():
        try:
            for ev in events:
                yield json.dumps(ev, ensure_ascii=False, default=str) + "\n"
        except (PdfError, ValueError, LookupError, providers.ProviderError) as e:
            yield json.dumps({"type": "error", "stage": "error", "error": str(e)}, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001
            log.exception("Error inesperado")
            yield json.dumps({"type": "error", "stage": "error", "error": f"Error interno: {e}"}) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- Estado y configuración ----------

@app.get("/api/health")
def health():
    cfg = providers.load()
    status = {"db": True, "limits": {"max_pages": settings.max_pages, "max_file_mb": settings.max_file_mb}}
    try:
        models = providers.list_models(cfg["llm_provider"], cfg)
        status["llm"] = {"ok": True, "provider": cfg["llm_provider"], "models": len(models)}
    except providers.ProviderError as e:
        status["llm"] = {"ok": False, "provider": cfg["llm_provider"], "error": str(e)}
    return status


@app.get("/api/settings")
def get_settings():
    return providers.public(providers.load())


@app.put("/api/settings")
def put_settings(patch: dict):
    return providers.public(providers.save(patch))


@app.get("/api/models/{provider}")
def models(provider: str):
    if provider not in providers.PROVIDERS:
        raise HTTPException(404, "Proveedor desconocido")
    try:
        return {"models": providers.list_models(provider)}
    except providers.ProviderError as e:
        raise HTTPException(502, str(e)) from e


# ---------- Documentos ----------

@app.get("/api/documents")
def list_documents():
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, name, pages, size_bytes, chunk_count, embed_provider, embed_model, created_at "
            "FROM documents ORDER BY created_at DESC"
        ).fetchall()
    return [rag.serialize_doc(r) for r in rows]


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)):
    name = Path(file.filename or "documento.pdf").name
    data = await file.read(settings.max_file_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(413, f"El archivo supera {settings.max_file_mb} MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "Solo se aceptan archivos PDF.")
    return ndjson(rag.ingest(name, data))


@app.delete("/api/documents/{doc_id}", status_code=204)
def delete_document(doc_id: UUID):
    with db.conn() as c:
        c.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


@app.get("/api/documents/{doc_id}/map")
def document_map(doc_id: UUID):
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, idx, page, x, y, left(content, 220) AS preview FROM chunks "
            "WHERE document_id = %s ORDER BY idx",
            (doc_id,),
        ).fetchall()
    return rows


@app.get("/api/documents/{doc_id}/messages")
def messages(doc_id: UUID):
    with db.conn() as c:
        return c.execute(
            "SELECT id, role, content, sources, created_at FROM messages "
            "WHERE document_id = %s ORDER BY id",
            (doc_id,),
        ).fetchall()


@app.delete("/api/documents/{doc_id}/messages", status_code=204)
def clear_messages(doc_id: UUID):
    with db.conn() as c:
        c.execute("DELETE FROM messages WHERE document_id = %s", (doc_id,))


class Ask(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.post("/api/documents/{doc_id}/chat")
def chat(doc_id: UUID, body: Ask):
    return ndjson(rag.answer(str(doc_id), body.message.strip()))


# ---------- Frontend compilado ----------

static = Path(settings.static_dir)
if static.is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = (static / path).resolve()
        if path and target.is_file() and static.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(static / "index.html")
