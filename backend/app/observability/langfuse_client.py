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
from datetime import datetime, timezone
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.config import settings

logger = logging.getLogger(__name__)

_langfuse_handler = None
_langfuse_client = None


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


def _init_langfuse_client():
    """
    Lazy-init direct Langfuse client for custom system spans.
    This is best-effort and never raises to callers.
    """
    global _langfuse_client
    if not settings.LANGFUSE_ENABLED:
        return None
    if _langfuse_client is not None:
        return _langfuse_client
    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        logger.info("Langfuse client initialized for system spans")
    except Exception as exc:
        logger.warning("Langfuse client init failed for system spans: %s", exc)
    return _langfuse_client


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


def emit_langfuse_system_span(
    *,
    span_name: str,
    session_id: str,
    user_id: Optional[str] = None,
    started_at_monotonic: Optional[float] = None,
    ended_at_monotonic: Optional[float] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """
    Emit a custom span for non-LLM system work (graph node/API/runtime).
    Compatible with Langfuse disabled mode and unknown SDK runtime differences.
    """
    if not settings.LANGFUSE_ENABLED:
        return

    duration_ms: Optional[float] = None
    if started_at_monotonic is not None and ended_at_monotonic is not None:
        duration_ms = max((ended_at_monotonic - started_at_monotonic) * 1000.0, 0.0)

    payload_meta: dict[str, Any] = {
        "span_name": span_name,
        "duration_ms": duration_ms,
    }
    if metadata:
        payload_meta.update(metadata)

    client = _init_langfuse_client()
    if client is None:
        return

    try:
        # Langfuse Python SDK v2 style.
        trace = client.trace(
            name="smilecare-system",
            session_id=session_id,
            user_id=user_id,
            metadata={"source": "backend-system"},
        )
        span = trace.span(
            name=span_name,
            start_time=datetime.now(timezone.utc),
            metadata=payload_meta,
        )
        span.end(end_time=datetime.now(timezone.utc), metadata=payload_meta)
        client.flush()
    except Exception as exc:
        logger.debug("Langfuse system span emit skipped (%s): %s", span_name, exc)
