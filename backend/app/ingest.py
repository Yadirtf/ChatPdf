"""Extracción por tipo de fuente: todo termina como fragmentos de texto listos para vectorizar.

    PDF     → texto por página  +  descripción de cada imagen, gráfico o página escaneada
    imagen  → descripción
    audio   → transcripción con marcas de tiempo
    video   → transcripción  +  descripción de los fotogramas clave

Las descripciones las escribe el modelo de visión elegido y se guardan junto a la imagen
(tabla assets, por hash): si la misma imagen vuelve a aparecer, no se vuelve a describir.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import db, providers
from .chunking import split_text
from .config import settings
from .imaging import Picture, prepare
from .media import MediaError, fmt_time, group_segments, keyframes, probe, transcribe, transcript_around
from .pdf import read_pdf

KINDS = {
    **dict.fromkeys((".pdf",), "pdf"),
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"), "image"),
    **dict.fromkeys((".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac"), "audio"),
    **dict.fromkeys((".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"), "video"),
}

Progress = Callable[[str, float | None], None]


def kind_of(path: Path) -> str | None:
    return KINDS.get(path.suffix.lower())


@dataclass
class Piece:
    modality: str                 # text | image | speech | frame
    content: str
    page: int | None = None
    t_start: float | None = None
    t_end: float | None = None
    asset_id: int | None = None

    def locator(self) -> str:
        if self.page:
            return f"página {self.page}"
        if self.t_start is not None:
            end = f"–{fmt_time(self.t_end)}" if self.t_end and self.t_end - self.t_start >= 1 else ""
            return fmt_time(self.t_start) + end
        return ""


MODALITY_LABEL = {"text": "texto", "image": "imagen", "speech": "transcripción", "frame": "fotograma"}


# ---------- Visión ----------

_VISION_PROMPT = """Describe esta imagen para que se pueda encontrar con búsquedas y responder preguntas sobre ella.
- Empieza diciendo qué es: fotografía, diagrama, gráfico, tabla, captura de pantalla, ilustración, logotipo…
- Describe lo que se ve: objetos, personas, acciones, colores, disposición.
- Transcribe el texto legible tal cual aparece.
- Si es un gráfico o una tabla, indica qué mide, los ejes, los valores y la tendencia principal.
No inventes lo que no se ve. Responde en español, en un solo párrafo de máximo 150 palabras."""

_PAGE_PROMPT = """Esta es una página escaneada de un documento. Transcribe todo su texto en orden de lectura,
conservando títulos y listas. Si además contiene figuras o fotos, descríbelas en una línea al final.
Responde solo con el contenido de la página."""

_ROLE_HINT = {
    "image": "una imagen incrustada en {where}",
    "figure": "un gráfico o diagrama de {where}",
    "frame": "un fotograma de {where}",
    "page": "{where}",
}


def describe(pic: Picture, where: str, cfg: dict) -> tuple[int, str]:
    """Devuelve (asset_id, descripción), reutilizando la descripción si la imagen ya se conocía."""
    with db.conn() as c:
        row = c.execute("SELECT id, description FROM assets WHERE sha256 = %s", (pic.sha256,)).fetchone()
    if row and row["description"]:
        return row["id"], row["description"]

    provider = cfg["vision_provider"]
    prompt = _PAGE_PROMPT if pic.role == "page" else _VISION_PROMPT
    context = f"La imagen es {_ROLE_HINT[pic.role].format(where=where)}."
    if pic.context and pic.role != "page":
        label = "Se oye alrededor de ese momento" if pic.role == "frame" else "Texto que la acompaña"
        context += f" {label} (úsalo solo para interpretarla, no lo repitas): «{pic.context}»"
    text = providers.complete(
        [{"role": "user", "content": f"{prompt}\n\n{context}", "images": [pic.data]}],
        provider, cfg, max_tokens=900 if pic.role == "page" else 350,
    )
    if not text:
        raise providers.ProviderError(
            f"El modelo de visión ({providers.model_label(provider, cfg)}) no devolvió ninguna descripción. "
            "¿Es un modelo multimodal? En llama.cpp hace falta cargarlo con su --mmproj."
        )
    with db.conn() as c:
        asset = c.execute(
            """INSERT INTO assets(sha256, mime, width, height, data, description, described_by)
               VALUES (%s, 'image/jpeg', %s, %s, %s, %s, %s)
               ON CONFLICT (sha256) DO UPDATE SET description = EXCLUDED.description,
                                                   described_by = EXCLUDED.described_by
               RETURNING id""",
            (pic.sha256, pic.width, pic.height, pic.data, text, providers.model_label(provider, cfg)),
        ).fetchone()
    return asset["id"], text


def _describe_all(items: list[tuple[Picture, str, Piece]], cfg: dict, progress: Progress, stage: str) -> list[Piece]:
    out = []
    for i, (pic, where, piece) in enumerate(items):
        progress(stage, i / len(items))
        piece.asset_id, piece.content = describe(pic, where, cfg)
        out.append(piece)
    return out


# ---------- Por tipo de fuente ----------

def _pdf(path: Path, name: str, cfg: dict, progress: Progress) -> tuple[list[Piece], dict]:
    progress("Leyendo páginas", None)
    content = read_pdf(path, settings.max_pages, settings.max_images_per_pdf)
    pieces = [
        Piece("text", text, page=no)
        for no, page in enumerate(content.pages, start=1)
        for text in (split_text(page, settings.chunk_size, settings.chunk_overlap) if page else [])
    ]
    visual = [
        # Una página escaneada transcrita es texto del documento; lo demás es una imagen.
        (pic, f"la página {no} de «{name}»", Piece("text" if pic.role == "page" else "image", "", page=no))
        for no, pic in content.pictures
    ]
    for piece in _describe_all(visual, cfg, progress, "Describiendo imágenes"):
        if piece.modality == "image":
            pieces.append(piece)
            continue
        # La transcripción de una página escaneada puede ser larga: se fragmenta como el texto normal.
        pieces += [Piece("text", t, page=piece.page, asset_id=piece.asset_id)
                   for t in split_text(piece.content, settings.chunk_size, settings.chunk_overlap)]
    pieces.sort(key=lambda p: (p.page, p.modality != "text"))
    meta = {"pages": len(content.pages), "images": sum(p.role != "page" for _, p in content.pictures),
            "scanned_pages": sum(p.role == "page" for _, p in content.pictures)}
    if content.skipped_images:
        meta["skipped_images"] = content.skipped_images
    if not pieces:
        raise ValueError("No se encontró texto ni imágenes en el PDF.")
    return pieces, meta


def _image(path: Path, name: str, cfg: dict, progress: Progress) -> tuple[list[Piece], dict]:
    pic = prepare(path.read_bytes())
    if not pic:
        raise ValueError("La imagen es demasiado pequeña, plana o no se pudo abrir.")
    pieces = _describe_all([(pic, f"el archivo «{name}»", Piece("image", ""))], cfg, progress, "Describiendo la imagen")
    return pieces, {"width": pic.width, "height": pic.height}


def _media(path: Path, name: str, cfg: dict, progress: Progress, video: bool) -> tuple[list[Piece], dict]:
    info = probe(path)
    if info.duration > settings.max_media_minutes * 60:
        raise MediaError(f"Dura {fmt_time(info.duration)}; el máximo es {settings.max_media_minutes} minutos.")
    meta: dict = {"duration": round(info.duration, 1)}
    segments = []
    if info.has_audio:
        progress("Transcribiendo", 0.0)
        segments, meta["language"] = transcribe(path, info.duration, lambda f: progress("Transcribiendo", f))
    pieces = [Piece("speech", g.text, t_start=g.start, t_end=g.end) for g in group_segments(segments, settings.chunk_size)]

    if video and info.has_video:
        progress("Buscando escenas", None)
        frames = keyframes(path, info.duration, settings.max_frames_per_video,
                           lambda t: transcript_around(segments, t))
        visual = [(pic, f"el video «{name}», en el {fmt_time(t)}", Piece("frame", "", t_start=t)) for t, pic in frames]
        pieces += _describe_all(visual, cfg, progress, "Describiendo escenas")
        pieces.sort(key=lambda p: p.t_start or 0)
        meta["frames"] = len(frames)
    if not pieces:
        raise MediaError("No se detectó voz ni imagen que indexar.")
    return pieces, meta


def extract(kind: str, path: Path, name: str, cfg: dict, progress: Progress) -> tuple[list[Piece], dict]:
    if kind == "pdf":
        return _pdf(path, name, cfg, progress)
    if kind == "image":
        return _image(path, name, cfg, progress)
    return _media(path, name, cfg, progress, video=kind == "video")


def embed_title(name: str, modality: str, locator: str) -> str:
    """Título que acompaña a cada fragmento al vectorizarlo (EmbeddingGemma lo aprovecha)."""
    return " · ".join(x for x in (name, locator, MODALITY_LABEL[modality]) if x)
