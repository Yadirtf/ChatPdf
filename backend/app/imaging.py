"""Normalización de imágenes antes de describirlas y guardarlas."""

import hashlib
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, ImageStat

MIN_SIDE = 96          # iconos, viñetas y separadores no aportan nada
MAX_SIDE = 1024        # suficiente para los codificadores de visión (Gemma usa 896 px)


@dataclass
class Picture:
    data: bytes        # JPEG normalizado: lo que ve el modelo y lo que muestra la UI
    width: int
    height: int
    sha256: str
    context: str = ""  # texto cercano (página, transcripción) que orienta la descripción
    role: str = "image"  # image | figure (gráfico vectorial) | page (página escaneada) | frame


def prepare(source: bytes | Image.Image, context: str = "", role: str = "image") -> Picture | None:
    """Devuelve la imagen como JPEG RGB acotado, o None si es demasiado pequeña o plana."""
    try:
        img = source if isinstance(source, Image.Image) else Image.open(io.BytesIO(source))
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception:
        return None
    if min(img.size) < MIN_SIDE:
        return None
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    else:
        img = img.convert("RGB")
    # Fondos, degradados y rellenos de color sólido: casi sin variación.
    if max(ImageStat.Stat(img.convert("L").resize((64, 64))).stddev) < 6:
        return None
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, optimize=True)
    data = buf.getvalue()
    return Picture(data, img.width, img.height, hashlib.sha256(data).hexdigest(), context, role)


def signature(img: Image.Image) -> Image.Image:
    """Miniatura en grises para comparar fotogramas sin coste."""
    return img.convert("L").resize((24, 24), Image.Resampling.BILINEAR)


def difference(a: Image.Image, b: Image.Image) -> float:
    """Diferencia media por píxel (0–255) entre dos firmas."""
    return float(np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).mean())
