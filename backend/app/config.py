from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


class Settings(BaseSettings):
    """Valores por defecto leídos del entorno. La UI puede sobrescribir los de los proveedores."""

    # Lee .env desde la raíz del proyecto o desde backend/ (el último gana).
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env", BACKEND / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql://astrolabio:astrolabio@localhost:5433/astrolabio"

    # Carpeta vigilada: todo lo que se copie aquí entra en la base de conocimiento.
    knowledge_dir: Path = ROOT / "conocimiento"
    scan_interval: int = 30  # segundos; red de seguridad por si se pierde un evento del sistema de archivos

    # Límites de lo que se acepta
    max_pages: int = 8
    max_file_mb: int = 200
    max_media_minutes: int = 15
    max_images_per_pdf: int = 12
    max_frames_per_video: int = 8

    # Fragmentación y recuperación
    chunk_size: int = 900
    chunk_overlap: int = 150
    top_k: int = 8
    # Si una pregunta general sobre una fuente cabe en este presupuesto, se envía la fuente completa.
    full_doc_chars: int = 9000
    # Imágenes recuperadas que se le muestran al modelo de chat si es multimodal.
    attach_images: int = 2

    # Embeddings: EmbeddingGemma servido por llama.cpp (llama-server --embeddings).
    # Una sola base vectorial: todas las fuentes comparten modelo y dimensión.
    embed_base_url: str = "http://127.0.0.1:8081/v1"
    embed_model: str = "embeddinggemma-300m"
    embed_api_key: str = ""
    embed_dim: int = 768

    # Chat y visión: "llamacpp" | "ollama" | "cloud"
    chat_provider: str = "llamacpp"
    vision_provider: str = "llamacpp"

    llamacpp_base_url: str = "http://127.0.0.1:8080/v1"
    llamacpp_chat_model: str = "gemma-3-4b-it"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "gemma4:e2b"
    ollama_num_ctx: int = 8192
    ollama_think: bool = False  # razonamiento previo de modelos como gemma4 (más lento)

    # Cualquier API compatible con OpenAI: OpenAI, Gemini, Groq, OpenRouter, Mistral...
    cloud_base_url: str = "https://api.openai.com/v1"
    cloud_api_key: str = ""
    cloud_chat_model: str = "gpt-4o-mini"

    # Transcripción local con faster-whisper (tiny, base, small, medium, large-v3-turbo)
    whisper_model: str = "small"
    whisper_language: str = ""  # vacío = detección automática
    whisper_device: str = "auto"  # "cpu", "cuda" o "auto"

    # Interfaz compilada. Vacío = usa backend/static (Docker) o frontend/dist (local).
    static_dir: str = ""

    @field_validator("knowledge_dir")
    @classmethod
    def _from_root(cls, v: Path) -> Path:
        # Una ruta relativa en .env se entiende desde la raíz del proyecto, no desde donde se lance la API.
        return v if v.is_absolute() else ROOT / v


settings = Settings()
