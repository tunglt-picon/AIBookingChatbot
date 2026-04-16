"""
Langfuse Observability Integration
====================================

Wraps every LangChain/LangGraph call with Langfuse tracing when enabled.

Usage in agent nodes:
    callbacks = get_langfuse_callback(config)
    response = await llm.ainvoke(prompt, config={"callbacks": callbacks})

Traces are grouped per session (session_id = LangGraph thread_id).
"""

import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.config import settings

logger = logging.getLogger(__name__)

_langfuse_handler = None


def _init_langfuse():
    global _langfuse_handler
    if not settings.LANGFUSE_ENABLED:
        return None
    if _langfuse_handler is not None:
        return _langfuse_handler
    try:
        from langfuse.callback import CallbackHandler

        _langfuse_handler = CallbackHandler(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        logger.info("Langfuse callback handler initialized")
    except ImportError:
        logger.warning("langfuse package not installed; observability disabled")
    except Exception as exc:
        logger.warning(f"Langfuse init failed: {exc}")
    return _langfuse_handler


def get_langfuse_callback(
    config: Optional[RunnableConfig] = None,
    trace_name: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> list:
    """
    Return a list of callbacks to pass to LangChain `.ainvoke()`.
    Returns an empty list when Langfuse is disabled (zero overhead).
    """
    handler = _init_langfuse()
    if handler is None:
        return []

    # Create a per-request handler with metadata if Langfuse supports it
    try:
        from langfuse.callback import CallbackHandler

        thread_id = None
        if config and "configurable" in config:
            thread_id = config["configurable"].get("thread_id")

        per_request = CallbackHandler(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
            trace_name=trace_name or "dental-agent",
            session_id=session_id or thread_id,
            user_id=user_id,
        )
        return [per_request]
    except Exception:
        return [handler]
