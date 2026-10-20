"""Lectura de PDF: texto por página e imágenes que merecen una descripción.

Además de las imágenes incrustadas (fotos, capturas), se recortan los gráficos dibujados
con vectores (barras, diagramas) y se renderizan las páginas escaneadas sin texto, para que
el modelo de visión también pueda leerlas.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .chunking import clean
from .imaging import Picture, prepare


class PdfError(ValueError):
    pass


@dataclass
class PdfContent:
    pages: list[str]
    pictures: list[tuple[int, Picture]] = field(default_factory=list)  # (página, imagen)
    skipped_images: int = 0


MIN_SHOWN_PT = 60       # tamaño mínimo en la página (puntos) para que una imagen cuente
REPEATED_ON = 3         # una imagen en 3 o más páginas es un logo o un fondo
SCAN_TEXT_CHARS = 40    # menos texto que esto: la página se trata como escaneada
CLIP_DPI = 144


def _coverage(page: pymupdf.Page, xref: int) -> float:
    """Fracción de la página que ocupa la imagen (0 si es demasiado pequeña para contar)."""
    rects = page.get_image_rects(xref)
    if not rects or max(min(r.width, r.height) for r in rects) < MIN_SHOWN_PT:
        return 0.0
    return max(r.width * r.height for r in rects) / (page.rect.width * page.rect.height)


def _embedded(doc: pymupdf.Document, xref: int) -> bytes | None:
    try:
        info = doc.extract_image(xref)
        if info and info.get("smask") == 0 and info["ext"] in ("png", "jpeg", "jpg", "bmp", "gif", "tiff", "webp"):
            return info["image"]
        # Con máscara de transparencia, CMYK o formatos raros (JPX, JBIG2): que PyMuPDF lo componga.
        pix = pymupdf.Pixmap(doc, xref)
        if info and info.get("smask"):
            pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, info["smask"]))
        if pix.colorspace and pix.colorspace.n not in (1, 3):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        return pix.tobytes("png")
    except Exception:
        return None


def _drawn_figures(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Zonas con muchos trazos vectoriales que ocupan una parte apreciable de la página."""
    try:
        clusters = page.cluster_drawings()
    except Exception:
        return []
    w, h = page.rect.width, page.rect.height
    return [r for r in clusters if r.width > w * 0.3 and r.height > h * 0.15]


def read_pdf(path: Path, max_pages: int, max_images: int) -> PdfContent:
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise PdfError("El archivo no es un PDF válido.") from e
    with doc:
        if doc.needs_pass and not doc.authenticate(""):
            raise PdfError("El PDF está protegido con contraseña.")
        n = doc.page_count
        if n == 0:
            raise PdfError("El PDF no tiene páginas.")
        if n > max_pages:
            raise PdfError(f"El PDF tiene {n} páginas; el máximo permitido es {max_pages}.")

        pages = [clean(p.get_text("text", sort=True)) for p in doc]
        uses = Counter(x for p in doc for x in {i[0] for i in p.get_images(full=True)})

        candidates: list[tuple[int, bytes, str]] = []
        for no, page in enumerate(doc, start=1):
            found = False
            scanned = len(pages[no - 1]) < SCAN_TEXT_CHARS
            for xref in dict.fromkeys(i[0] for i in page.get_images(full=True)):
                cover = _coverage(page, xref)
                if uses[xref] >= REPEATED_ON or not cover:
                    continue
                data = _embedded(doc, xref)
                if data:
                    # Una imagen que llena una página sin texto es un escaneo: se transcribe.
                    candidates.append((no, data, "page" if scanned and cover > 0.6 else "image"))
                    found = True
            for rect in _drawn_figures(page):
                candidates.append((no, page.get_pixmap(clip=rect, dpi=CLIP_DPI).tobytes("png"), "figure"))
                found = True
            if not found and scanned:
                candidates.append((no, page.get_pixmap(dpi=CLIP_DPI).tobytes("png"), "page"))

    out = PdfContent(pages)
    seen: set[str] = set()
    for no, data, role in candidates:
        pic = prepare(data, pages[no - 1][:600], role)
        if not pic or pic.sha256 in seen:
            continue
        seen.add(pic.sha256)
        if len(out.pictures) >= max_images:
            out.skipped_images += 1
            continue
        out.pictures.append((no, pic))
    return out
