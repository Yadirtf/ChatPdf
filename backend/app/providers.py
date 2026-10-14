"""Proveedores de LLM y embeddings.

- `ollama`: modelos abiertos ejecutados localmente (opción gratuita por defecto).
- `openai`: cualquier API compatible con OpenAI con API key (OpenAI, Groq, OpenRouter,
  Together, Mistral, LM Studio, vLLM...). Sirve tanto para claves de pago como gratuitas.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import httpx
from psycopg.types.json import Jsonb

from . import db
from .config import settings

PROVIDERS = ("ollama", "openai")
TIMEOUT = httpx.Timeout(connect=10, read=300, write=60, pool=10)


class ProviderError(RuntimeError):
    pass


def defaults() -> dict:
    return {
        "llm_provider": settings.llm_provider,
        "embed_provider": settings.embed_provider,
        "ollama": {
            "base_url": settings.ollama_base_url,
            "chat_model": settings.ollama_chat_model,
            "embed_model": settings.ollama_embed_model,
            "think": "true" if settings.ollama_think else "false",
        },
        "openai": {
            "base_url": settings.openai_base_url,
            "api_key": settings.openai_api_key,
            "chat_model": settings.openai_chat_model,
            "embed_model": settings.openai_embed_model,
        },
    }


def load() -> dict:
    cfg = defaults()
    with db.conn() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key = 'providers'").fetchone()
    if row:
        stored = row["value"]
        for key in ("llm_provider", "embed_provider"):
            if stored.get(key) in PROVIDERS:
                cfg[key] = stored[key]
        for p in PROVIDERS:
            cfg[p].update({k: v for k, v in (stored.get(p) or {}).items() if k in cfg[p]})
    for p in PROVIDERS:
        cfg[p]["base_url"] = _fit_host(cfg[p]["base_url"])
    return cfg


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _fit_host(url: str) -> str:
    """Ajusta una URL guardada al lugar donde corre la API.

    host.docker.internal solo existe dentro de Docker y localhost dentro de un
    contenedor es el propio contenedor. Si la URL se guardó en un modo y la API
    corre en el otro, se traduce para que siga llegando al mismo servicio del PC.
    """
    parts = urlparse(url)
    host = parts.hostname or ""
    if IN_DOCKER and host in _LOCAL_HOSTS:
        new = "host.docker.internal"
    elif not IN_DOCKER and host == "host.docker.internal":
        # 127.0.0.1 y no "localhost": en Windows localhost resuelve primero a ::1 y Ollama solo escucha en IPv4.
        new = "127.0.0.1"
    else:
        return url
    netloc = new + (f":{parts.port}" if parts.port else "")
    return parts._replace(netloc=netloc).geturl()


def save(patch: dict) -> dict:
    cfg = load()
    for key in ("llm_provider", "embed_provider"):
        if patch.get(key) in PROVIDERS:
            cfg[key] = patch[key]
    for p in PROVIDERS:
        for k, v in (patch.get(p) or {}).items():
            if k not in cfg[p] or v is None:
                continue
            # La UI nunca recibe la clave completa: si la devuelve enmascarada, se conserva.
            if k == "api_key" and isinstance(v, str) and v.startswith("•"):
                continue
            cfg[p][k] = _fit_host(str(v).strip()) if k == "base_url" else str(v).strip()
    with db.conn() as c:
        c.execute(
            "INSERT INTO app_settings(key, value) VALUES ('providers', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (Jsonb(cfg),),
        )
    return cfg


def public(cfg: dict) -> dict:
    out = json.loads(json.dumps(cfg))
    key = out["openai"].get("api_key") or ""
    out["openai"]["api_key"] = ("••••••••" + key[-4:]) if key else ""
    out["openai"]["has_key"] = bool(key)
    return out


IN_DOCKER = Path("/.dockerenv").exists()


def _unreachable(provider: str, base_url: str, err: Exception) -> ProviderError:
    msg = f"No se pudo conectar con {provider} ({base_url}): {err}"
    host = urlparse(base_url).hostname or ""
    if IN_DOCKER and host in ("localhost", "127.0.0.1", "::1"):
        # Dentro del contenedor, localhost es el propio contenedor, no tu PC.
        msg += (". La app corre en Docker: usa http://host.docker.internal:"
                f"{urlparse(base_url).port or 11434} en el botón motor para llegar al Ollama de tu PC.")
    return ProviderError(msg)


def _openai_headers(p: dict) -> dict:
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


# ---------- Embeddings ----------

def embed(texts: list[str], provider: str, model: str, cfg: dict | None = None) -> list[list[float]]:
    cfg = cfg or load()
    p = cfg[provider]
    try:
        if provider == "ollama":
            r = httpx.post(
                f"{p['base_url'].rstrip('/')}/api/embed",
                json={"model": model, "input": texts},
                timeout=TIMEOUT,
            )
            _raise(r, "Ollama")
            return r.json()["embeddings"]
        r = httpx.post(
            f"{p['base_url'].rstrip('/')}/embeddings",
            headers=_openai_headers(p),
            json={"model": model, "input": texts},
            timeout=TIMEOUT,
        )
        _raise(r, "API")
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
    except httpx.HTTPError as e:
        raise _unreachable(provider, p["base_url"], e) from e


# ---------- Chat (streaming) ----------

def _ollama_chat(p: dict, messages: list[dict], think: bool | None) -> Iterator[tuple[str, str]]:
    body = {
        "model": p["chat_model"],
        "messages": messages,
        "stream": True,
        # Mantiene el modelo cargado entre preguntas para no recargarlo cada vez.
        "keep_alive": "30m",
        # El contexto por defecto de Ollama (2–4k tokens) recorta los fragmentos del PDF.
        "options": {"temperature": 0.2, "num_ctx": settings.ollama_num_ctx},
    }
    if think is not None:
        body["think"] = think
    with httpx.stream("POST", f"{p['base_url'].rstrip('/')}/api/chat", json=body, timeout=TIMEOUT) as r:
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


def chat_stream(messages: list[dict], cfg: dict | None = None) -> Iterator[tuple[str, str]]:
    """Emite ("thinking", texto) mientras el modelo razona y ("content", texto) con la respuesta."""
    cfg = cfg or load()
    provider = cfg["llm_provider"]
    p = cfg[provider]
    try:
        if provider == "ollama":
            # Los modelos con razonamiento (Gemma 4, Qwen 3, DeepSeek R1...) piensan antes de
            # responder; en CPU eso puede tardar minutos sin mostrar nada. Por defecto se apaga.
            think = str(p.get("think", "false")).lower() == "true"
            try:
                yield from _ollama_chat(p, messages, think)
            except ProviderError as e:
                # Los modelos sin razonamiento rechazan el parámetro "think": se reintenta sin él.
                if "think" not in str(e).lower():
                    raise
                yield from _ollama_chat(p, messages, None)
            return

        with httpx.stream(
            "POST",
            f"{p['base_url'].rstrip('/')}/chat/completions",
            headers=_openai_headers(p),
            json={"model": p["chat_model"], "messages": messages, "stream": True, "temperature": 0.2},
            timeout=TIMEOUT,
        ) as r:
            if not r.is_success:
                r.read()
                _raise(r, "API")
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                data = json.loads(payload)
                choices = data.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield "thinking", reasoning
                    if delta.get("content"):
                        yield "content", delta["content"]
    except httpx.HTTPError as e:
        raise _unreachable(provider, p["base_url"], e) from e


# ---------- Utilidades ----------

def list_models(provider: str, cfg: dict | None = None) -> list[str]:
    cfg = cfg or load()
    p = cfg[provider]
    try:
        if provider == "ollama":
            r = httpx.get(f"{p['base_url'].rstrip('/')}/api/tags", timeout=10)
            _raise(r, "Ollama")
            return sorted(m["name"] for m in r.json().get("models", []))
        r = httpx.get(f"{p['base_url'].rstrip('/')}/models", headers=_openai_headers(p), timeout=10)
        _raise(r, "API")
        return sorted(m["id"] for m in r.json().get("data", []))
    except httpx.HTTPError as e:
        raise _unreachable(provider, p["base_url"], e) from e
