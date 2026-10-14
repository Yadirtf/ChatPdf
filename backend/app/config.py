from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Valores por defecto leídos del entorno. La UI puede sobrescribir los del proveedor."""

    # Lee .env desde la raíz del proyecto o desde backend/ (el último gana).
    model_config = SettingsConfigDict(
        env_file=(BACKEND.parent / ".env", BACKEND / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql://chatpdf:chatpdf@localhost:5433/chatpdf"

    max_pages: int = 8
    max_file_mb: int = 20
    chunk_size: int = 900
    chunk_overlap: int = 150
    top_k: int = 6
    # Si una pregunta general cabe en este presupuesto, se envía el documento completo.
    full_doc_chars: int = 9000

    # Proveedor por defecto: modelo abierto local vía Ollama
    llm_provider: str = "ollama"  # "ollama" | "openai"
    embed_provider: str = "ollama"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "gemma4:e2b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_num_ctx: int = 8192
    ollama_think: bool = False  # razonamiento previo de modelos como gemma4 (más lento)

    # Cualquier API compatible con OpenAI (OpenAI, Groq, OpenRouter, Together, LM Studio...)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    openai_embed_model: str = "text-embedding-3-small"

    # Interfaz compilada. Vacío = usa backend/static (Docker) o frontend/dist (local).
    static_dir: str = ""


settings = Settings()
