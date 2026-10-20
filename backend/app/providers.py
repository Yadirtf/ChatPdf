"""Modelos: embeddings, chat y visión.

- Embeddings: EmbeddingGemma vía llama.cpp (`llama-server --embeddings`), o cualquier
  endpoint `/v1/embeddings` compatible con OpenAI.
- Chat y visión, a elegir por separado:
  - `llamacpp`: `llama-server` local (API compatible con OpenAI; multimodal con `--mmproj`).
  - `ollama`: Ollama local (API nativa, para controlar contexto y razonamiento).
  - `cloud`: cualquier API compatible con OpenAI con API key, de pago o gratuita
    (OpenAI, Gemini, Groq, OpenRouter, Mistral...).

Los mensajes se escriben en un formato propio, `{"role", "content", "images": [bytes]}`,
y cada adaptador lo traduce al de su API.
"""

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import httpx
from psycopg.types.json import Jsonb

from . import db
from .config import settings

PROVIDERS = ("llamacpp", "ollama", "cloud")
LABELS = {"llamacpp": "llama.cpp", "ollama": "Ollama", "cloud": "API en la nube"}
TIMEOUT = httpx.Timeout(connect=10, read=300, write=60, pool=10)
SECRET_KEYS = ("api_key",)
IN_DOCKER = Path("/.dockerenv").exists()


class ProviderError(RuntimeError):
    pass


# ---------- Configuración ----------

def defaults() -> dict:
    return {
        "chat_provider": settings.chat_provider,
        "vision_provider": settings.vision_provider,
        "llamacpp": {"base_url": settings.llamacpp_base_url, "chat_model": settings.llamacpp_chat_model},
        "ollama": {
            "base_url": settings.ollama_base_url,
            "chat_model": settings.ollama_chat_model,
            "think": "true" if settings.ollama_think else "false",
        },
        "cloud": {
            "base_url": settings.cloud_base_url,
            "api_key": settings.cloud_api_key,
            "chat_model": settings.cloud_chat_model,
        },
        "embed": {
            "base_url": settings.embed_base_url,
            "model": settings.embed_model,
            "api_key": settings.embed_api_key,
        },
    }


SECTIONS = (*PROVIDERS, "embed")


def _merge(cfg: dict, patch: dict, *, from_ui: bool) -> dict:
    for key in ("chat_provider", "vision_provider"):
        if patch.get(key) in PROVIDERS:
            cfg[key] = patch[key]
    for section in SECTIONS:
        for k, v in (patch.get(section) or {}).items():
            if k not in cfg[section] or v is None:
                continue
            # La UI nunca recibe una clave completa: si la devuelve enmascarada, se conserva.
            if from_ui and k in SECRET_KEYS and str(v).startswith("•"):
                continue
            cfg[section][k] = str(v).strip()
    for section in SECTIONS:
        cfg[section]["base_url"] = _fit_host(cfg[section]["base_url"])
    return cfg


def load() -> dict:
    with db.conn() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key = 'providers'").fetchone()
    return _merge(defaults(), row["value"] if row else {}, from_ui=False)


def save(patch: dict) -> dict:
    cfg = _merge(load(), patch, from_ui=True)
    with db.conn() as c:
        c.execute(
            "INSERT INTO app_settings(key, value) VALUES ('providers', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (Jsonb(cfg),),
        )
    return cfg


def public(cfg: dict) -> dict:
    out = json.loads(json.dumps(cfg))
    for section in SECTIONS:
        for k in SECRET_KEYS:
            if k in out[section]:
                key = out[section][k] or ""
                out[section][k] = ("••••••••" + key[-4:]) if key else ""
                out[section]["has_key"] = bool(key)
    return out


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _fit_host(url: str) -> str:
    """Ajusta una URL guardada al lugar donde corre la API.

    host.docker.internal solo existe dentro de Docker y localhost dentro de un contenedor es
    el propio contenedor. Si la URL se guardó en un modo y la API corre en el otro, se traduce
    para que siga llegando al mismo servicio del PC.
    """
    parts = urlparse(url)
    host = parts.hostname or ""
    if IN_DOCKER and host in _LOCAL_HOSTS:
        new = "host.docker.internal"
    elif not IN_DOCKER and host == "host.docker.internal":
        # 127.0.0.1 y no "localhost": en Windows localhost resuelve primero a ::1 y los servidores
        # locales (Ollama, llama-server) suelen escuchar solo en IPv4.
        new = "127.0.0.1"
    else:
        return url
    netloc = new + (f":{parts.port}" if parts.port else "")
    return parts._replace(netloc=netloc).geturl()


# ---------- HTTP ----------

def _unreachable(who: str, base_url: str, err: Exception) -> ProviderError:
    return ProviderError(f"No se pudo conectar con {who} ({base_url}): {err}")


def _headers(p: dict) -> dict:
    h = {"Content-Type": "application/json"}
    if p.get("api_key"):
        h["Authorization"] = f"Bearer {p['api_key']}"
    return h


def _raise(r: httpx.Response, who: str):
    if r.is_success:
        return
    try:
        detail = r.json()
        detail = detail.get("error", detail)
        if isinstance(detail, dict):
            detail = detail.get("message", detail)
    except Exception:
        detail = r.text[:300]
    raise ProviderError(f"{who} respondió {r.status_code}: {detail}")


def _url(p: dict, path: str) -> str:
    return p["base_url"].rstrip("/") + path


# ---------- Embeddings ----------

def _is_embeddinggemma(model: str) -> bool:
    m = model.lower().replace("-", "")
    return "embeddinggemma" in m or "gemmaembed" in m


def _embed(texts: list[str], cfg: dict) -> list[list[float]]:
    p = cfg["embed"]
    try:
        r = httpx.post(_url(p, "/embeddings"), headers=_headers(p),
                       json={"model": p["model"], "input": texts}, timeout=TIMEOUT)
        _raise(r, "El servidor de embeddings")
    except httpx.HTTPError as e:
        raise _unreachable("el servidor de embeddings", p["base_url"], e) from e
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    vectors = [d["embedding"] for d in data]
    if vectors and len(vectors[0]) != settings.embed_dim:
        raise ProviderError(
            f"El modelo de embeddings devuelve {len(vectors[0])} dimensiones y la base espera "
            f"{settings.embed_dim}. Ajusta EMBED_DIM={len(vectors[0])} en .env y reinicia: "
            "la base se vuelve a vectorizar sola."
        )
    return vectors


def embed_documents(items: list[tuple[str, str]], cfg: dict) -> list[list[float]]:
    """Vectoriza fragmentos `(título, texto)`.

    EmbeddingGemma se entrenó con prefijos de tarea: los documentos llevan
    "title: … | text: …" y las consultas "task: search result | query: …".
    Usarlos mejora la recuperación de forma notable frente a enviar el texto desnudo.
    """
    if _is_embeddinggemma(cfg["embed"]["model"]):
        texts = [f"title: {title or 'none'} | text: {text}" for title, text in items]
    else:
        texts = [text for _, text in items]
    return _embed(texts, cfg)


def embed_query(question: str, cfg: dict) -> list[float]:
    text = f"task: search result | query: {question}" if _is_embeddinggemma(cfg["embed"]["model"]) else question
    return _embed([text], cfg)[0]


# ---------- Chat y visión ----------

def _jpeg_url(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def _openai_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if not m.get("images"):
            out.append({"role": m["role"], "content": m["content"]})
            continue
        parts = [{"type": "image_url", "image_url": {"url": _jpeg_url(img)}} for img in m["images"]]
        out.append({"role": m["role"], "content": [*parts, {"type": "text", "text": m["content"]}]})
    return out


def _openai_chat(p: dict, who: str, messages: list[dict], max_tokens: int | None) -> Iterator[tuple[str, str]]:
    body = {"model": p["chat_model"], "messages": _openai_messages(messages), "stream": True, "temperature": 0.2}
    if max_tokens:
        body["max_tokens"] = max_tokens
    with httpx.stream("POST", _url(p, "/chat/completions"), headers=_headers(p), json=body, timeout=TIMEOUT) as r:
        if not r.is_success:
            r.read()
            _raise(r, who)
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            data = json.loads(payload)
            if data.get("error"):
                raise ProviderError(f"{who}: {data['error']}")
            choices = data.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    yield "thinking", reasoning
                if delta.get("content"):
                    yield "content", delta["content"]


def _ollama_chat(p: dict, messages: list[dict], think: bool | None, max_tokens: int | None) -> Iterator[tuple[str, str]]:
    options = {"temperature": 0.2, "num_ctx": settings.ollama_num_ctx}
    if max_tokens:
        options["num_predict"] = max_tokens
    body = {
        "model": p["chat_model"],
        "messages": [
            {"role": m["role"], "content": m["content"],
             **({"images": [base64.b64encode(i).decode() for i in m["images"]]} if m.get("images") else {})}
            for m in messages
        ],
        "stream": True,
        # Mantiene el modelo cargado entre preguntas para no recargarlo cada vez.
        "keep_alive": "30m",
        # El contexto por defecto de Ollama (2–4k tokens) recorta los fragmentos recuperados.
        "options": options,
    }
    if think is not None:
        body["think"] = think
    with httpx.stream("POST", _url(p, "/api/chat"), json=body, timeout=TIMEOUT) as r:
        if not r.is_success:
            r.read()
            _raise(r, "Ollama")
        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            if data.get("error"):
                raise ProviderError(f"Ollama: {data['error']}")
            msg = data.get("message") or {}
            if msg.get("thinking"):
                yield "thinking", msg["thinking"]
            if msg.get("content"):
                yield "content", msg["content"]
            if data.get("done"):
                return


def chat_stream(messages: list[dict], provider: str, cfg: dict, *,
                max_tokens: int | None = None, think: bool | None = None) -> Iterator[tuple[str, str]]:
    """Emite ("thinking", texto) mientras el modelo razona y ("content", texto) con la respuesta."""
    p = cfg[provider]
    try:
        if provider != "ollama":
            yield from _openai_chat(p, LABELS[provider], messages, max_tokens)
            return
        # Los modelos con razonamiento (Gemma 4, Qwen 3...) piensan antes de responder; en CPU
        # eso puede tardar minutos sin mostrar nada. Por defecto se apaga.
        if think is None:
            think = str(p.get("think", "false")).lower() == "true"
        try:
            yield from _ollama_chat(p, messages, think, max_tokens)
        except ProviderError as e:
            # Los modelos sin razonamiento rechazan el parámetro "think": se reintenta sin él.
            if "think" not in str(e).lower():
                raise
            yield from _ollama_chat(p, messages, None, max_tokens)
    except httpx.HTTPError as e:
        raise _unreachable(LABELS[provider], p["base_url"], e) from e


def complete(messages: list[dict], provider: str, cfg: dict, max_tokens: int = 400) -> str:
    return "".join(t for kind, t in chat_stream(messages, provider, cfg, max_tokens=max_tokens, think=False)
                   if kind == "content").strip()


def model_label(provider: str, cfg: dict) -> str:
    return f"{LABELS[provider]} · {cfg[provider]['chat_model']}"


# ---------- Utilidades ----------

def list_models(section: str, cfg: dict | None = None, timeout: float = 10) -> list[str]:
    cfg = cfg or load()
    p = cfg[section]
    who = "el servidor de embeddings" if section == "embed" else LABELS[section]
    try:
        if section == "ollama":
            r = httpx.get(_url(p, "/api/tags"), timeout=timeout)
            _raise(r, who)
            return sorted(m["name"] for m in r.json().get("models", []))
        r = httpx.get(_url(p, "/models"), headers=_headers(p), timeout=timeout)
        _raise(r, who)
        return sorted(m["id"] for m in r.json().get("data", []))
    except httpx.HTTPError as e:
        raise _unreachable(who, p["base_url"], e) from e
