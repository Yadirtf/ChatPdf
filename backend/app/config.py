from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Valores por defecto leídos del entorno. La UI puede sobrescribir los del proveedor."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://chatpdf:chatpdf@localhost:5432/chatpdf"

    max_pages: int = 8
    max_file_mb: int = 20
    chunk_size: int = 900
    chunk_overlap: int = 150
    top_k: int = 5

    # Proveedor por defecto: modelo abierto local vía Ollama
    llm_provider: str = "ollama"  # "ollama" | "openai"
    embed_provider: str = "ollama"

    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2:3b"
    ollama_embed_model: str = "nomic-embed-text"

    # Cualquier API compatible con OpenAI (OpenAI, Groq, OpenRouter, Together, LM Studio...)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    openai_embed_model: str = "text-embedding-3-small"

    static_dir: str = "static"


settings = Settings()
