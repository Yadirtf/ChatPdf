"""Proveedores de LLM y embeddings.

- `ollama`: modelos abiertos ejecutados localmente (opción gratuita por defecto).
- `openai`: cualquier API compatible con OpenAI con API key (OpenAI, Groq, OpenRouter,
  Together, Mistral, LM Studio, vLLM...). Sirve tanto para claves de pago como gratuitas.
"""

import json
from collections.abc import Iterator

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
    return cfg


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
            cfg[p][k] = str(v).strip()
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
        raise ProviderError(f"No se pudo conectar con {provider} ({p['base_url']}): {e}") from e


# ---------- Chat (streaming) ----------

def chat_stream(messages: list[dict], cfg: dict | None = None) -> Iterator[str]:
    cfg = cfg or load()
    provider = cfg["llm_provider"]
    p = cfg[provider]
    try:
        if provider == "ollama":
            with httpx.stream(
                "POST",
                f"{p['base_url'].rstrip('/')}/api/chat",
                json={"model": p["chat_model"], "messages": messages, "stream": True,
                      "options": {"temperature": 0.2}},
                timeout=TIMEOUT,
            ) as r:
                if not r.is_success:
                    r.read()
                    _raise(r, "Ollama")
                for line in r.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise ProviderError(f"Ollama: {data['error']}")
                    piece = (data.get("message") or {}).get("content")
                    if piece:
                        yield piece
                    if data.get("done"):
                        return
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
                    piece = (choices[0].get("delta") or {}).get("content")
                    if piece:
                        yield piece
    except httpx.HTTPError as e:
        raise ProviderError(f"No se pudo conectar con {provider} ({p['base_url']}): {e}") from e


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
        raise ProviderError(f"No se pudo conectar con {provider}: {e}") from e
