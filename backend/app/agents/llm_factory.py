"""
LLM Factory – returns the appropriate ChatModel based on LLM_PROVIDER config.

Supports:
  - "google"           → ChatGoogleGenerativeAI (Gemini API — mặc định gemini-2.5-flash-lite)
  - "ollama"           → ChatOllama  (local Ollama server)
  - "openai"           → ChatOpenAI  (OpenAI API)
  - "openai_compatible" → ChatOpenAI with custom base_url
                          (Together.ai, vLLM, LM Studio, Groq, …)

Root Orchestrator và Dental Specialist đều dùng **chat văn bản**; cấu hình model trong .env.
"""

import logging
from functools import lru_cache
from typing import Literal

from langchain_core.language_models import BaseChatModel

from app.config import settings

logger = logging.getLogger(__name__)


ModelRole = Literal["root", "specialist"]


def _ollama_httpx_timeout():
    import httpx

    return httpx.Timeout(
        connect=settings.OLLAMA_CONNECT_TIMEOUT,
        read=settings.OLLAMA_READ_TIMEOUT,
        write=settings.OLLAMA_READ_TIMEOUT,
        pool=10.0,
    )


def _build_ollama(model_name: str) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    timeout = _ollama_httpx_timeout()
    thinking = settings.OLLAMA_THINKING
    logger.info(
        "[llm] ChatOllama model=%r base_url=%s keep_alive=%r thinking=%s "
        "connect_timeout=%ss read_timeout=%ss",
        model_name,
        settings.OLLAMA_BASE_URL,
        settings.OLLAMA_KEEP_ALIVE,
        thinking,
        settings.OLLAMA_CONNECT_TIMEOUT,
        settings.OLLAMA_READ_TIMEOUT,
    )
    kwargs: dict = {
        "model": model_name,
        "base_url": settings.OLLAMA_BASE_URL,
        "temperature": 0.3,
        "client_kwargs": {"timeout": timeout},
        "keep_alive": settings.OLLAMA_KEEP_ALIVE,
    }
    # reasoning=False stops hidden "thinking" chains on Qwen3.5-class models (huge latency win).
    if not thinking:
        kwargs["reasoning"] = False
    return ChatOllama(**kwargs)


def _build_openai(model_name: str, api_key: str) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        temperature=0.3,
        streaming=True,
    )


def _build_openai_compatible(model_name: str) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        base_url=settings.OPENAI_COMPATIBLE_BASE_URL,
        api_key=settings.OPENAI_COMPATIBLE_API_KEY,
        temperature=0.3,
        streaming=True,
    )


def _build_google(model_name: str) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    key = (settings.GOOGLE_API_KEY or "").strip()
    if not key:
        raise ValueError(
            "LLM_PROVIDER=google cần GOOGLE_API_KEY trong .env (lấy tại https://aistudio.google.com/apikey )"
        )
    logger.info("[llm] ChatGoogleGenerativeAI model=%r", model_name)
    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=key,
        temperature=0.3,
        streaming=True,
    )


def create_llm(role: ModelRole) -> BaseChatModel:
    """Create an LLM for the given agent role based on current settings."""
    provider = settings.LLM_PROVIDER.lower()

    if role == "root":
        if provider == "google":
            return _build_google(settings.GOOGLE_ROOT_MODEL)
        if provider == "ollama":
            return _build_ollama(settings.ROOT_MODEL_NAME)
        elif provider == "openai":
            return _build_openai(settings.OPENAI_ROOT_MODEL, settings.OPENAI_API_KEY)
        elif provider == "openai_compatible":
            return _build_openai_compatible(settings.OPENAI_COMPATIBLE_ROOT_MODEL)

    elif role == "specialist":
        if provider == "google":
            return _build_google(settings.GOOGLE_SPECIALIST_MODEL)
        if provider == "ollama":
            return _build_ollama(settings.SPECIALIST_MODEL_NAME)
        elif provider == "openai":
            return _build_openai(settings.OPENAI_SPECIALIST_MODEL, settings.OPENAI_API_KEY)
        elif provider == "openai_compatible":
            return _build_openai_compatible(settings.OPENAI_COMPATIBLE_SPECIALIST_MODEL)

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER!r}")


# Cached singletons – created once per process lifetime
@lru_cache(maxsize=1)
def get_root_llm() -> BaseChatModel:
    model = create_llm("root")
    logger.info(
        "[llm] root model ready provider=%s (see GOOGLE_* / ROOT_MODEL_* / OPENAI_* in config)",
        settings.LLM_PROVIDER,
    )
    return model


@lru_cache(maxsize=1)
def get_specialist_llm() -> BaseChatModel:
    model = create_llm("specialist")
    logger.info(
        "[llm] specialist (text) model ready provider=%s",
        settings.LLM_PROVIDER,
    )
    return model
