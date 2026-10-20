"""Consulta semántica sobre toda la base y respuesta con citas a los fragmentos usados."""

import re
from collections.abc import Iterator
from itertools import chain

import numpy as np
from psycopg.types.json import Jsonb

from . import db, providers
from .config import settings
from .ingest import MODALITY_LABEL, Piece
from .library import library, project

SYSTEM_PROMPT = """Eres Astrolabio, un asistente que responde preguntas sobre una base de conocimiento formada por documentos PDF, imágenes, audios y videos.
Respondes usando únicamente los fragmentos numerados que vienen en el mensaje del usuario.

Qué es cada fragmento:
- «texto»: texto de un PDF.
- «imagen» y «fotograma»: descripción de una imagen de un PDF, de una imagen suelta o de una escena de video, escrita por un modelo de visión. Si además recibes la imagen, mírala para precisar.
- «transcripción»: lo que se dice en un audio o un video, con su marca de tiempo.

Cómo responder:
- Explica con claridad y con tus propias palabras; usa puntos o pasos cuando ayude.
- Cita cada dato con el número de su fragmento entre corchetes, por ejemplo [2] o [1][4].
- Si un dato sale de una imagen o de un video, dilo: «en el gráfico de la página 3…», «hacia el 01:20 del video…».
- Si te preguntan por una página o un momento concreto, explica qué contiene y sus datos importantes.
- Si los fragmentos no contienen la respuesta, dilo con claridad y menciona qué temas sí cubren. No inventes.
- Responde en el mismo idioma de la pregunta."""

CANDIDATES = 40   # por cada búsqueda (vectorial y por palabras) antes de fusionar


# ---------- Comprensión de la pregunta ----------

_ORDINALS = {
    "primera": 1, "primer": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5,
    "sexta": 6, "séptima": 7, "septima": 7, "octava": 8,
}
_PAGE_WORD = r"(?:p[áa]gina|p[áa]g\.?|hoja|page|p\.)"
_GENERAL = re.compile(
    r"\b(resum\w*|de qu[ée] (trata|habla|se trata)|tema (principal|central)|ideas? (principal|clave|central)\w*|"
    r"qu[ée] (contiene|hay en)|todo el (documento|audio|video)|completo|panorama|"
    r"explica(me)? (el|la) (documento|audio|video|imagen)|summar\w*|overview|what is (it|this) about)\b",
    re.IGNORECASE,
)
_VISUAL = re.compile(r"\b(imagen|imágenes|imagenes|foto\w*|figura|gráfic\w*|grafic\w*|diagrama|tabla|esquema|"
                     r"escena|fotograma|se ve|muestra|aparece|color\w*|dibujo|ilustraci\w*|captura)\b", re.IGNORECASE)


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


def parse_times(question: str) -> list[float]:
    """Detecta momentos: "01:20", "1:05:10", "minuto 3", "al final"."""
    text = question.lower()
    out = [int(h or 0) * 3600 + int(m) * 60 + int(s)
           for h, m, s in re.findall(r"\b(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)\b", text)]
    out += [int(m) * 60 for m in re.findall(r"\bminuto\s+(\d{1,3})\b(?!:)", text)]
    return sorted(set(float(t) for t in out))


# ---------- Recuperación ----------

_COLUMNS = ("c.id, c.source_id, c.idx, c.modality, c.page, c.t_start, c.t_end, c.content, c.asset_id, "
            "s.name, s.kind")


def search(question: str, qvec: np.ndarray, scope: list[str] | None, limit: int) -> list[dict]:
    """Búsqueda híbrida en PostgreSQL: vecinos HNSW (coseno) + texto completo, fusionados con RRF.

    La consulta por palabras usa OR entre términos: una pregunta en lenguaje natural casi nunca
    contiene todas sus palabras en un mismo fragmento.
    """
    with db.conn() as c, c.transaction():
        c.execute("SET LOCAL hnsw.ef_search = 100")
        if scope and db.iterative_scan:
            c.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        return c.execute(
            f"""
            WITH q AS (
                SELECT to_tsquery('spanish', replace(plainto_tsquery('spanish', %(text)s)::text, ' & ', ' | ')) AS ts
            ),
            vec AS (
                SELECT id, dist, row_number() OVER (ORDER BY dist) AS r FROM (
                    SELECT id, embedding <=> %(q)s AS dist FROM chunks
                    WHERE embedding IS NOT NULL AND (%(scope)s::uuid[] IS NULL OR source_id = ANY(%(scope)s))
                    ORDER BY embedding <=> %(q)s LIMIT %(n)s
                ) v
            ),
            kw AS (
                SELECT id, row_number() OVER (ORDER BY rank DESC) AS r FROM (
                    SELECT id, ts_rank_cd(tsv, q.ts) AS rank FROM chunks, q
                    WHERE tsv @@ q.ts AND (%(scope)s::uuid[] IS NULL OR source_id = ANY(%(scope)s))
                    ORDER BY rank DESC LIMIT %(n)s
                ) k
            )
            SELECT {_COLUMNS},
                   1 - (c.embedding <=> %(q)s) AS score,
                   coalesce(1.0 / (60 + vec.r), 0) + coalesce(1.0 / (60 + kw.r), 0) AS rrf
            FROM (SELECT id FROM vec UNION SELECT id FROM kw) hit
            JOIN chunks c ON c.id = hit.id JOIN sources s ON s.id = c.source_id
            LEFT JOIN vec ON vec.id = hit.id LEFT JOIN kw ON kw.id = hit.id
            ORDER BY rrf DESC LIMIT %(limit)s
            """,
            {"q": qvec, "text": question, "scope": scope, "n": CANDIDATES, "limit": limit},
        ).fetchall()


def _source_chunks(source_id: str, qvec: np.ndarray) -> list[dict]:
    with db.conn() as c:
        return c.execute(
            f"SELECT {_COLUMNS}, 1 - (c.embedding <=> %s) AS score, 0.0 AS rrf "
            "FROM chunks c JOIN sources s ON s.id = c.source_id WHERE c.source_id = %s ORDER BY c.idx",
            (qvec, source_id),
        ).fetchall()


def _overview(scope: list[str] | None, qvec: np.ndarray) -> list[dict]:
    """Los primeros fragmentos de cada fuente: un panorama de toda la base."""
    with db.conn() as c:
        return c.execute(
            f"""SELECT * FROM (
                    SELECT {_COLUMNS}, 1 - (c.embedding <=> %(q)s) AS score, 0.0 AS rrf,
                           row_number() OVER (PARTITION BY c.source_id ORDER BY c.idx) AS nth
                    FROM chunks c JOIN sources s ON s.id = c.source_id
                    WHERE s.status = 'ready' AND (%(scope)s::uuid[] IS NULL OR c.source_id = ANY(%(scope)s))
                ) t WHERE nth <= 2 ORDER BY nth, name""",
            {"q": qvec, "scope": scope},
        ).fetchall()


def _budget(rows: list[dict], limit: int) -> list[dict]:
    chosen, used, seen = [], 0, set()
    for r in rows:
        if r["id"] in seen or used + len(r["content"]) > limit:
            continue
        seen.add(r["id"])
        chosen.append(r)
        used += len(r["content"])
    return chosen


def select(question: str, qvec: np.ndarray, sources: list[dict]) -> tuple[list[dict], str, str]:
    """Decide qué ve el modelo. Devuelve (fragmentos, nota para el modelo, estado para la UI)."""
    scope = [str(s["id"]) for s in sources] or None
    ranked = search(question, qvec, scope, limit=24)
    general = bool(_GENERAL.search(question))

    if len(sources) == 1:
        src = sources[0]
        rows = _source_chunks(str(src["id"]), qvec)
        rank = {r["id"]: r["rrf"] for r in ranked}
        for r in rows:
            r["rrf"] = rank.get(r["id"], 0.0)
        total_chars = sum(len(r["content"]) for r in rows)

        pages = parse_pages(question, src["meta"].get("pages", 0)) if src["kind"] == "pdf" else []
        if pages:
            hits = [r for r in rows if r["page"] in pages]
            extra = [r for r in sorted(rows, key=lambda r: -r["rrf"]) if r["page"] not in pages][:2]
            label = ", ".join(map(str, pages))
            note = f"El usuario pregunta por la página {label} de «{src['name']}»; abajo está todo lo que contiene."
            if not hits:
                note += f" Esa página no existe o está vacía: el documento tiene {src['meta'].get('pages')} páginas."
            return hits + extra, note, f"Leyendo la página {label}"

        times = parse_times(question) if src["kind"] in ("audio", "video") else []
        if times:
            hits = [r for r in rows if any((r["t_start"] or 0) - 30 <= t <= (r["t_end"] or r["t_start"] or 0) + 30
                                           for t in times)]
            label = ", ".join(Piece("speech", "", t_start=t).locator() for t in times)
            return hits, f"El usuario pregunta por el momento {label} de «{src['name']}».", f"Escuchando el {label}"

        if total_chars <= settings.full_doc_chars:
            return rows, f"Abajo está el contenido completo de «{src['name']}».", "Leyendo la fuente completa"
        if general:
            firsts = list({r["page"] or r["idx"]: r for r in reversed(rows)}.values())[::-1]
            chosen = _budget(firsts + sorted(rows, key=lambda r: -r["rrf"]), settings.full_doc_chars)
            return sorted(chosen, key=lambda r: r["idx"]), "Abajo hay una muestra de toda la fuente.", "Recorriendo la fuente"
        return [r for r in ranked if r["source_id"] == src["id"]][: settings.top_k], \
            "Abajo están los fragmentos más relacionados con la pregunta.", "Leyendo los fragmentos más relevantes"

    if general:
        chosen = _budget(_overview(scope, qvec) + ranked, settings.full_doc_chars)
        return chosen, "Abajo hay un panorama de las fuentes de la base y lo más relacionado con la pregunta.", \
            "Recorriendo la base de conocimiento"
    return ranked[: settings.top_k], "Abajo están los fragmentos más relacionados con la pregunta.", \
        "Leyendo los fragmentos más relevantes"


# ---------- Respuesta ----------

def _header(n: int, r: dict) -> str:
    locator = Piece(r["modality"], "", r["page"], r["t_start"], r["t_end"]).locator()
    return f"[{n}] {r['name']}" + (f" · {locator}" if locator else "") + f" · {MODALITY_LABEL[r['modality']]}"


def serialize_source(n: int, r: dict) -> dict:
    return {
        "n": n, "chunk_id": r["id"], "source_id": str(r["source_id"]), "name": r["name"], "kind": r["kind"],
        "modality": r["modality"], "page": r["page"], "t_start": r["t_start"], "t_end": r["t_end"],
        "asset_id": r["asset_id"], "score": round(float(r["score"] or 0), 4), "content": r["content"],
    }


def _images(chosen: list[dict], question: str) -> dict[int, bytes]:
    """Las imágenes mejor rankeadas por número de fragmento, para que un modelo multimodal las mire."""
    if not settings.attach_images:
        return {}
    ranked = sorted(enumerate(chosen, start=1), key=lambda nr: -nr[1]["rrf"])
    visual = [(n, r) for n, r in ranked if r["asset_id"]][: settings.attach_images]
    # Solo si la pregunta habla de algo visual o una imagen está entre lo más relevante.
    top = {r["id"] for _, r in ranked[:3]}
    if not visual or not (_VISUAL.search(question) or any(r["id"] in top for _, r in visual)):
        return {}
    with db.conn() as c:
        rows = c.execute("SELECT id, data FROM assets WHERE id = ANY(%s)", ([r["asset_id"] for _, r in visual],)).fetchall()
    data = {r["id"]: bytes(r["data"]) for r in rows}
    return {n: data[r["asset_id"]] for n, r in visual if r["asset_id"] in data}


def answer(question: str, scope: list[str]) -> Iterator[dict]:
    with db.conn() as c:
        sources = c.execute("SELECT id, name, kind, meta FROM sources WHERE id = ANY(%s::uuid[])",
                            (scope,)).fetchall() if scope else []
        if scope and not sources:
            raise LookupError("Las fuentes elegidas ya no existen.")
        ready = c.execute("SELECT count(*) AS n FROM chunks WHERE embedding IS NOT NULL").fetchone()["n"]
        history = c.execute(
            "SELECT role, content FROM (SELECT id, role, content FROM messages ORDER BY id DESC LIMIT 4) h ORDER BY id"
        ).fetchall()
    if not ready:
        raise LookupError("La base de conocimiento está vacía: copia archivos en la carpeta o súbelos.")

    yield {"type": "status", "text": "Buscando en la base"}
    cfg = providers.load()
    qvec = np.asarray(providers.embed_query(question, cfg), dtype=np.float32)
    chosen, note, status = select(question, qvec, sources)

    items = [serialize_source(n, r) for n, r in enumerate(chosen, start=1)]
    query = project(qvec, library.projection) if library.projection else None
    yield {"type": "sources", "query": query, "sources": items}
    yield {"type": "status", "text": status}

    context = "\n\n".join(f"{_header(n, r)}\n{r['content']}" for n, r in enumerate(chosen, start=1))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        # Los números de cita de respuestas anteriores no corresponden a los fragmentos de ahora.
        text = m["content"] if m["role"] == "user" else re.sub(r"\[\d+\]", "", m["content"])[:800]
        messages.append({"role": m["role"], "content": text})
    images = _images(chosen, question)
    messages.append({
        "role": "user",
        "content": (
            f"{note}\n\n--- FRAGMENTOS ---\n{context or '(ninguno)'}\n--- FIN DE LOS FRAGMENTOS ---\n\n"
            + (f"Se adjuntan las imágenes de los fragmentos {', '.join(f'[{n}]' for n in images)}, en ese orden.\n"
               if images else "")
            + f"Pregunta: {question}"
        ),
        "images": list(images.values()),
    })

    provider = cfg["chat_provider"]
    parts: list[str] = []
    thinking = False
    try:
        stream = providers.chat_stream(messages, provider, cfg)
        first = next(stream, None)
    except providers.ProviderError:
        if not images:
            raise
        # El modelo de chat no acepta imágenes: se responde solo con las descripciones.
        messages[-1]["images"] = []
        stream = providers.chat_stream(messages, provider, cfg)
        first = next(stream, None)
    for kind, piece in chain([first] if first else [], stream):
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
        raise providers.ProviderError("El modelo terminó sin escribir una respuesta. Prueba de nuevo o cambia de modelo.")
    slim = {"query": query, "items": items, "scope": scope, "model": providers.model_label(provider, cfg)}
    with db.conn() as c:
        c.execute("INSERT INTO messages(role, content) VALUES ('user', %s)", (question,))
        row = c.execute("INSERT INTO messages(role, content, sources) VALUES ('assistant', %s, %s) RETURNING id",
                        (reply, Jsonb(slim))).fetchone()
    yield {"type": "done", "message_id": row["id"]}
