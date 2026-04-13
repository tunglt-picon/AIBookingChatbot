from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Application ──────────────────────────────
    APP_NAME: str = "AI Dental Booking Chatbot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    # Logging: DEBUG | INFO | WARNING | ERROR
    LOG_LEVEL: str = "INFO"

    # ── Security ─────────────────────────────────
    SECRET_KEY: str = "change-this-secret-key"
    JWT_SECRET_KEY: str = "change-this-jwt-secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 hours

    # ── Database ─────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://dental_user:dental_pass@localhost:5432/dental_db"

    # ── Redis (tùy chọn — stack hiện không import Redis trong code; Compose vẫn có service redis để dev tay) ──
    REDIS_URL: str = "redis://localhost:6379"

    # ── LLM Configuration ────────────────────────
    # Provider: "google" | "ollama" | "openai" | "openai_compatible"
    LLM_PROVIDER: str = "google"

    # Google AI Studio (Gemini) — https://aistudio.google.com/apikey
    GOOGLE_API_KEY: str = ""
    # Stable 2.5 (2.0 Flash đã deprecated cho user mới — xem https://ai.google.dev/gemini-api/docs/models )
    # flash-lite: nhanh + rẻ nhất dòng 2.5; đổi sang gemini-2.5-flash nếu cần suy luận/JSON tốt hơn
    GOOGLE_ROOT_MODEL: str = "gemini-2.5-flash-lite"
    GOOGLE_SPECIALIST_MODEL: str = "gemini-2.5-flash-lite"

    # Ollama settings
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    ROOT_MODEL_NAME: str = "qwen2.5:7b"
    SPECIALIST_MODEL_NAME: str = "qwen2.5:7b"
    # httpx timeouts for Ollama (prevents infinite hang if server is down or model stuck loading)
    OLLAMA_CONNECT_TIMEOUT: float = 10.0
    OLLAMA_READ_TIMEOUT: float = 300.0  # first inference / large models on CPU can take minutes
    # Keep model loaded in VRAM/RAM between requests (e.g. "15m", "30m", or -1 for indefinite)
    OLLAMA_KEEP_ALIVE: str = "15m"
    # Qwen3.5 etc.: "thinking" adds many hidden tokens → very slow. False disables it (LangChain → Ollama).
    OLLAMA_THINKING: bool = False

    # OpenAI settings
    OPENAI_API_KEY: str = ""
    OPENAI_ROOT_MODEL: str = "gpt-4o-mini"
    OPENAI_SPECIALIST_MODEL: str = "gpt-4o"

    # OpenAI-compatible endpoint (Together.ai, vLLM, LM Studio, etc.)
    OPENAI_COMPATIBLE_BASE_URL: str = ""
    OPENAI_COMPATIBLE_API_KEY: str = "dummy"
    OPENAI_COMPATIBLE_ROOT_MODEL: str = "qwen2.5:7b"
    OPENAI_COMPATIBLE_SPECIALIST_MODEL: str = "gpt-4o-mini"

    # ── Agent Behaviour ───────────────────────────
    MAX_FOLLOW_UP_QUESTIONS: int = 3

    # ── Langfuse ─────────────────────────────────
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # ── File Upload ───────────────────────────────
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 10

    # ── CORS ─────────────────────────────────────
    CORS_ORIGINS: List[str] = ["*"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v):
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
            except Exception:
                parsed = [item.strip() for item in v.split(",")]
        else:
            parsed = list(v)

        # "null" origin is sent by browsers when opening file:// directly.
        # Add it automatically so local dev without a web server still works.
        if "*" not in parsed and "null" not in parsed:
            parsed.append("null")

        return parsed

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
