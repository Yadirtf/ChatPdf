"""División de texto en fragmentos (chunks) con solapamiento."""

import re

_SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " "]


def clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"-\n(?=\w)", "", text)          # une palabras cortadas con guion
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


def split_text(text: str, size: int, overlap: int) -> list[str]:
    """Agrupa piezas hasta `size` caracteres; cada fragmento repite el final del anterior."""
    chunks: list[str] = []
    current = ""
    for piece in _split(text, size):
        if current and len(current) + len(piece) > size:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap else ""
            # empieza el solapamiento en un límite de palabra
            current = tail[tail.find(" ") + 1:] if " " in tail else tail
        current += piece
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if len(c) > 20] or chunks
