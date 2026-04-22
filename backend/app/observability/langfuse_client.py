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


def build_session_trace_id(session_id: str | int) -> str:
    return f"chat-session-{session_id}"


def build_phase_span_name(phase: str, name: str) -> str:
    """
    Standardize span names for better timeline readability.
    Example: "02.graph.node.classify_intent".
    """
    safe_phase = str(phase or "").strip()
    safe_name = str(name or "").strip().replace(" ", "_")
    if not safe_phase:
        safe_phase = "99.misc"
    if not safe_name:
        safe_name = "unnamed"
    return f"{safe_phase}.{safe_name}"


def normalize_tags(tags: Optional[list[str]]) -> list[str]:
    if not tags:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        txt = str(tag or "").strip().lower().replace(" ", "-")
        if txt and txt not in seen:
            seen.add(txt)
            out.append(txt)
    return out


def _safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(v) for v in value]
    return str(value)


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
    trace_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
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

        callback_kwargs: dict[str, Any] = {
            "public_key": settings.LANGFUSE_PUBLIC_KEY,
            "secret_key": settings.LANGFUSE_SECRET_KEY,
            "host": settings.LANGFUSE_HOST,
            "trace_name": trace_name or "dental-agent",
            "session_id": session_id or thread_id,
            "user_id": user_id,
        }
        if trace_id:
            callback_kwargs["trace_id"] = trace_id
        if metadata:
            callback_kwargs["metadata"] = _safe_json_value(metadata)
        normalized_tags = normalize_tags(tags)
        if normalized_tags:
            callback_kwargs["tags"] = normalized_tags

        try:
            per_request = CallbackHandler(**callback_kwargs)
        except TypeError:
            # Backward-compatible fallback for older callback SDK signatures.
            callback_kwargs.pop("trace_id", None)
            callback_kwargs.pop("metadata", None)
            callback_kwargs.pop("tags", None)
            per_request = CallbackHandler(**callback_kwargs)
        return [per_request]
    except Exception:
        return [handler]


def ensure_session_trace(
    *,
    session_id: str | int,
    user_id: Optional[str] = None,
    input_payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Upsert a parent trace per chat session and return stable trace_id.
    """
    if not settings.LANGFUSE_ENABLED:
        return None

    client = _init_langfuse_client()
    if client is None:
        return None

    trace_id = build_session_trace_id(session_id)
    try:
        trace_meta = {
            "source": "chat-session",
            "status": "in_progress",
            "level": "info",
        }
        if metadata:
            trace_meta.update(_safe_json_value(metadata))
        normalized_tags = normalize_tags(tags)
        trace = client.trace(
            id=trace_id,
            name="01.session.chat",
            session_id=str(session_id),
            user_id=user_id,
            input=_safe_json_value(input_payload),
            metadata=trace_meta,
            tags=normalized_tags or None,
        )
        trace.update(
            metadata=trace_meta,
            session_id=str(session_id),
            user_id=user_id,
            tags=normalized_tags or None,
        )
        client.flush()
        return trace_id
    except Exception as exc:
        logger.debug("Langfuse ensure_session_trace skipped (%s): %s", trace_id, exc)
        return None


def create_langfuse_span(
    *,
    trace_id: str,
    session_id: str,
    span_name: str,
    user_id: Optional[str] = None,
    parent_observation_id: Optional[str] = None,
    input_payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
) -> Any:
    """
    Create a long-lived span that can be ended later.
    Returns None if creation fails.
    """
    if not settings.LANGFUSE_ENABLED:
        return None
    client = _init_langfuse_client()
    if client is None:
        return None
    try:
        trace = client.trace(
            id=trace_id,
            name="01.session.chat",
            session_id=session_id,
            user_id=user_id,
            metadata={"source": "backend-system"},
        )
        span_kwargs: dict[str, Any] = {
            "name": span_name,
            "start_time": datetime.now(timezone.utc),
            "input": _safe_json_value(input_payload),
            "metadata": _safe_json_value(metadata or {}),
            "tags": normalize_tags(tags) or None,
        }
        if parent_observation_id:
            span_kwargs["parent_observation_id"] = parent_observation_id
        try:
            return trace.span(**span_kwargs)
        except TypeError:
            span_kwargs.pop("parent_observation_id", None)
            span_kwargs.pop("tags", None)
            return trace.span(**span_kwargs)
    except Exception as exc:
        logger.debug("Langfuse create span skipped (%s): %s", span_name, exc)
        return None


def end_langfuse_span(
    span: Any,
    *,
    output_payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    if span is None:
        return
    client = _init_langfuse_client()
    if client is None:
        return
    try:
        span.end(
            end_time=datetime.now(timezone.utc),
            output=_safe_json_value(output_payload),
            metadata=_safe_json_value(metadata or {}),
        )
        client.flush()
    except Exception as exc:
        logger.debug("Langfuse end span skipped: %s", exc)


def update_session_trace(
    *,
    trace_id: str,
    output_payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
) -> None:
    if not settings.LANGFUSE_ENABLED:
        return
    client = _init_langfuse_client()
    if client is None:
        return
    try:
        normalized_tags = normalize_tags(tags)
        trace = client.trace(id=trace_id)
        trace.update(
            output=_safe_json_value(output_payload),
            metadata=_safe_json_value(metadata or {}),
            tags=normalized_tags or None,
        )
        client.flush()
    except Exception as exc:
        logger.debug("Langfuse update_session_trace skipped (%s): %s", trace_id, exc)


def emit_langfuse_system_span(
    *,
    span_name: str,
    session_id: str,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    started_at_monotonic: Optional[float] = None,
    ended_at_monotonic: Optional[float] = None,
    input_payload: Optional[dict[str, Any]] = None,
    output_payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
    parent_observation_id: Optional[str] = None,
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
        "status": "success",
        "level": "info",
    }
    if metadata:
        payload_meta.update(_safe_json_value(metadata))

    client = _init_langfuse_client()
    if client is None:
        return

    try:
        resolved_trace_id = trace_id or build_session_trace_id(session_id)
        # Langfuse Python SDK v2 style.
        trace = client.trace(
            id=resolved_trace_id,
            name="01.session.chat",
            session_id=session_id,
            user_id=user_id,
            metadata={"source": "backend-system"},
        )
        normalized_tags = normalize_tags(tags)
        span_kwargs: dict[str, Any] = {
            "name": span_name,
            "start_time": datetime.now(timezone.utc),
            "input": _safe_json_value(input_payload),
            "metadata": payload_meta,
            "tags": normalized_tags or None,
            "parent_observation_id": parent_observation_id,
        }
        try:
            span = trace.span(**span_kwargs)
        except TypeError:
            span_kwargs.pop("parent_observation_id", None)
            span_kwargs.pop("tags", None)
            span = trace.span(**span_kwargs)
        span.end(
            end_time=datetime.now(timezone.utc),
            output=_safe_json_value(output_payload),
            metadata=payload_meta,
        )
        client.flush()
    except Exception as exc:
        logger.debug("Langfuse system span emit skipped (%s): %s", span_name, exc)
