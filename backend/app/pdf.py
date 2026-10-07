"""Lectura de PDF y división en fragmentos (chunks) con solapamiento."""

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PdfError(ValueError):
    pass


@dataclass
class Chunk:
    idx: int
    page: int
    content: str


def read_pages(data: bytes, max_pages: int) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:
                raise PdfError("El PDF está protegido con contraseña.") from e
        n = len(reader.pages)
    except PdfReadError as e:
        raise PdfError("El archivo no es un PDF válido.") from e
    if n == 0:
        raise PdfError("El PDF no tiene páginas.")
    if n > max_pages:
        raise PdfError(f"El PDF tiene {n} páginas; el máximo permitido es {max_pages}.")
    return [_clean(p.extract_text() or "") for p in reader.pages]


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"-\n(?=\w)", "", text)          # une palabras cortadas con guion
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " "]


def _split(text: str, size: int, seps=_SEPARATORS) -> list[str]:
    """Divide recursivamente por el separador más grande que deje piezas <= size."""
    if len(text) <= size:
        return [text]
    for i, sep in enumerate(seps):
        if sep in text:
            parts = text.split(sep)
            out = []
            for j, part in enumerate(parts):
                piece = part + (sep if j < len(parts) - 1 else "")
                if len(piece) > size:
                    out.extend(_split(piece, size, seps[i + 1:]))
                elif piece:
                    out.append(piece)
            return out
    return [text[i:i + size] for i in range(0, len(text), size)]


def chunk_pages(pages: list[str], size: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page_no, text in enumerate(pages, start=1):
        if not text:
            continue
        current = ""
        for piece in _split(text, size):
            if current and len(current) + len(piece) > size:
                chunks.append(Chunk(len(chunks), page_no, current.strip()))
                tail = current[-overlap:] if overlap else ""
                # empieza el solapamiento en un límite de palabra
                current = tail[tail.find(" ") + 1:] if " " in tail else tail
            current += piece
        if current.strip():
            chunks.append(Chunk(len(chunks), page_no, current.strip()))
    kept = [c for c in chunks if len(c.content) > 20] or chunks
    return [Chunk(i, c.page, c.content) for i, c in enumerate(kept)]
