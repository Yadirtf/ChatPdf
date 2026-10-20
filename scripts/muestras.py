"""Genera contenido de ejemplo en la carpeta de conocimiento.

    python scripts/muestras.py            # escribe en ./conocimiento/muestras

Crea un PDF de 3 páginas cuyo dato clave solo está en un gráfico (imagen incrustada), una
página con un diagrama dibujado con vectores y una infografía PNG suelta. Sirven para comprobar
que las preguntas sobre imágenes funcionan: el texto del PDF no contiene las cifras del gráfico.
Los audios y videos conviene grabarlos (ver README, «Contenido de ejemplo»).
"""

import io
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "conocimiento" / "muestras"

INK, BONE, EMBER, TEAL, GOLD = "#1b1915", "#f4efe6", "#ff5c39", "#2a9d8f", "#e9c46a"


def font(size: int):
    # La fuente por defecto de Pillow no tiene tildes: se busca una del sistema.
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def bar_chart() -> bytes:
    """Participación por fuente: las cifras solo existen en esta imagen."""
    data = [("Solar", 41, EMBER), ("Eólica", 27, TEAL), ("Hidráulica", 22, "#457b9d"), ("Biomasa", 10, GOLD)]
    img = Image.new("RGB", (1000, 640), BONE)
    d = ImageDraw.Draw(img)
    d.text((60, 36), "Nueva capacidad instalada en 2026 por fuente (%)", fill=INK, font=font(34))
    base, left, width = 560, 120, 150
    d.line((80, base, 940, base), fill=INK, width=3)
    for i, (name, value, color) in enumerate(data):
        x = left + i * 210
        top = base - value * 9
        d.rectangle((x, top, x + width, base), fill=color)
        d.text((x + 45, top - 46), f"{value}%", fill=INK, font=font(34))
        d.text((x + 10, base + 16), name, fill=INK, font=font(28))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def water_cycle() -> Image.Image:
    img = Image.new("RGB", (1100, 700), "#e8f1f2")
    d = ImageDraw.Draw(img)
    d.text((40, 30), "El ciclo del agua", fill=INK, font=font(48))
    d.ellipse((880, 60, 1020, 200), fill=GOLD)                       # sol
    d.text((900, 210), "Sol", fill=INK, font=font(28))
    d.rectangle((0, 520, 560, 700), fill="#3a86c8")                  # mar
    d.text((40, 600), "Océano", fill="white", font=font(32))
    d.polygon([(560, 700), (820, 330), (1100, 700)], fill="#6b8f71")  # montaña
    d.ellipse((380, 120, 700, 260), fill="white")                    # nube
    d.text((470, 170), "Nube", fill=INK, font=font(32))
    steps = [((180, 500), (360, 260), "1. Evaporación"), ((540, 330), (540, 330), "2. Condensación"),
             ((700, 260), (820, 420), "3. Precipitación"), ((760, 640), (520, 640), "4. Escorrentía")]
    for (a, b, label) in steps:
        if a != b:
            d.line((*a, *b), fill=EMBER, width=8)
            d.ellipse((b[0] - 12, b[1] - 12, b[0] + 12, b[1] + 12), fill=EMBER)
        d.text((a[0] - 60 if a == b else (a[0] + b[0]) // 2 + 14, (a[1] + b[1]) // 2 - 50), label, fill=INK, font=font(30))
    return img


def report(path: Path):
    doc = pymupdf.open()
    body = pymupdf.Rect(60, 120, 535, 780)

    p = doc.new_page()
    p.insert_text((60, 90), "Informe: transición energética 2026", fontsize=22)
    p.insert_textbox(body, (
        "Este informe resume cómo cambió la generación de electricidad durante 2026. "
        "La demanda creció un 4 % y, por primera vez, la mayor parte de la nueva capacidad "
        "instalada provino de fuentes renovables no convencionales.\n\n"
        "La sección 2 presenta la participación de cada fuente en la nueva capacidad. "
        "La sección 3 describe el esquema de almacenamiento con baterías que se propone para "
        "estabilizar la red en las horas de la tarde, cuando cae la producción solar.\n\n"
        "Recomendación principal: priorizar los proyectos que combinan generación solar con "
        "almacenamiento, porque reducen los cortes en la hora pico."
    ), fontsize=12)

    p = doc.new_page()
    p.insert_text((60, 90), "2. Nueva capacidad por fuente", fontsize=18)
    p.insert_textbox(pymupdf.Rect(60, 110, 535, 170),
                     "El gráfico muestra cómo se repartió la nueva capacidad instalada entre las distintas fuentes.",
                     fontsize=12)
    p.insert_image(pymupdf.Rect(60, 180, 535, 484), stream=bar_chart())

    p = doc.new_page()
    p.insert_text((60, 90), "3. Esquema de almacenamiento propuesto", fontsize=18)
    boxes = [("Paneles solares", 70, EMBER), ("Inversor", 235, TEAL), ("Baterías", 400, GOLD)]
    for label, x, color in boxes:
        r = pymupdf.Rect(x, 160, x + 130, 230)
        p.draw_rect(r, color=pymupdf.pdfcolor["black"], fill=pymupdf.utils.getColor("white"), width=1.5)
        p.draw_rect(pymupdf.Rect(x, 160, x + 130, 172), fill=tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)))
        p.insert_textbox(pymupdf.Rect(x + 8, 185, x + 125, 225), label, fontsize=12)
    for x in (200, 365):
        p.draw_line((x, 195), (x + 35, 195), width=2)
        p.draw_polyline([(x + 28, 189), (x + 35, 195), (x + 28, 201)], width=2)
    p.draw_line((465, 230), (465, 290), width=2)
    p.draw_rect(pymupdf.Rect(380, 290, 550, 350), width=1.5)
    p.insert_textbox(pymupdf.Rect(390, 305, 545, 345), "Red eléctrica (18:00 a 22:00)", fontsize=11)
    p.insert_textbox(pymupdf.Rect(60, 400, 535, 600), (
        "Durante el día los paneles cargan las baterías a través del inversor; en la tarde las "
        "baterías entregan esa energía a la red, justo cuando la demanda es máxima."
    ), fontsize=12)
    doc.save(path)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    report(OUT / "informe-transicion-energetica.pdf")
    water_cycle().save(OUT / "ciclo-del-agua.png")
    print(f"Contenido de ejemplo en {OUT}")
