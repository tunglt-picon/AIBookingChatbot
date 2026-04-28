"""
Chat API
=========

Endpoints:
  POST   /sessions                         – create a new session
  GET    /sessions                         – list patient sessions
  GET    /sessions/{id}                    – get session + message history
  POST   /sessions/{id}/messages           – send text (multipart); Returns: SSE stream
  POST   /sessions/{id}/close              – mark session COMPLETED
"""

import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Form, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.session import SenderType, SessionStatus
from app.observability.langfuse_client import (
    build_session_trace_id,
    create_langfuse_span,
    end_langfuse_span,
    emit_langfuse_system_span,
    ensure_session_trace,
    update_session_trace,
)
from app.schemas.chat import SessionResponse, SessionWithMessages
from app.services import auth_service, chat_service
logger = logging.getLogger(__name__)
router = APIRouter()

# LangGraph node names we care about in SSE / logs (match graph.py)
_GRAPH_NODES = frozenset({
    "classify_intent",
    "dental_specialist",
    "save_intake",
    "query_slots",
    "booking_prepare",
    "confirm_booking",
    "root_respond",
})


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content or "")


def _trim_text(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _extract_prompt_preview(model_input: object) -> str:
    """
    Best-effort prompt preview from chat model input payload.
    """
    if model_input is None:
        return ""
    if isinstance(model_input, str):
        return _trim_text(model_input, 1000)
    if isinstance(model_input, list):
        blocks: list[str] = []
        for item in model_input[-10:]:
            if isinstance(item, dict):
                role = str(item.get("role") or item.get("type") or "msg")
                text = _content_to_text(item.get("content")).strip()
                if text:
                    blocks.append(f"[{role}] {text}")
            else:
                text = _content_to_text(item).strip()
                if text:
                    blocks.append(text)
        return _trim_text("\n".join(blocks), 1000)
    if isinstance(model_input, dict):
        if "messages" in model_input:
            return _extract_prompt_preview(model_input.get("messages"))
        if "input" in model_input:
            return _extract_prompt_preview(model_input.get("input"))
    return _trim_text(str(model_input), 1000)


def _extract_message_text(msg: object) -> str:
    """Best-effort text extraction from LangChain message-ish object."""
    if msg is None:
        return ""
    if isinstance(msg, str):
        # Often format: "content='...'" from repr(), keep concise.
        if "content='" in msg:
            try:
                return msg.split("content='", 1)[1].split("'", 1)[0].strip()
            except Exception:
                return _trim_text(msg, 240)
        return _trim_text(msg, 240)
    content = getattr(msg, "content", None)
    if content is not None:
        return _trim_text(_content_to_text(content).strip(), 240)
    if isinstance(msg, dict):
        return _trim_text(_content_to_text(msg.get("content")).strip(), 240)
    return _trim_text(str(msg), 240)


def _extract_role(msg: object) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role") or msg.get("type") or "").lower()
    t = getattr(msg, "type", None)
    if isinstance(t, str):
        return t.lower()
    return ""


def _last_message_by_role(messages: list, wanted_role: str) -> str:
    for m in reversed(messages):
        role = _extract_role(m)
        if not role and isinstance(m, str):
            # Repr string fallback: detect specialist/ai roughly.
            if wanted_role == "ai" and "name='specialist_agent'" in m:
                return _extract_message_text(m)
            if wanted_role == "human" and "name='specialist_agent'" not in m:
                return _extract_message_text(m)
            continue
        if role in (wanted_role, "assistant" if wanted_role == "ai" else wanted_role):
            return _extract_message_text(m)
    return ""


def _normalize_node_input_payload(
    *,
    node: str,
    turn_id: str,
    session_id: int,
    patient_user_id: int,
    raw_state: object,
    user_message: str,
    llm_prompt: str,
) -> dict:
    state = raw_state if isinstance(raw_state, dict) else {}
    msgs = state.get("messages")
    messages = msgs if isinstance(msgs, list) else []
    last_human = _last_message_by_role(messages, "human") or _trim_text(user_message, 300)
    last_ai = _last_message_by_role(messages, "ai") or None

    if node == "classify_intent":
        node_state = {
            "intent": state.get("intent"),
            "current_agent": state.get("current_agent"),
            "skip_root_respond": state.get("skip_root_respond"),
            "messages_count": len(messages),
            "last_human_message": last_human or None,
            "last_ai_message": last_ai,
            "pending_confirmation_slot": state.get("pending_confirmation_slot"),
            "triage_complete": state.get("triage_complete"),
            "intake_id": state.get("intake_id"),
            "category_code": state.get("category_code"),
        }
    elif node == "dental_specialist":
        node_state = {
            "intent": state.get("intent"),
            "current_agent": state.get("current_agent"),
            "specialist_concluded": state.get("specialist_concluded"),
            "follow_up_count": state.get("follow_up_count"),
            "category_code": state.get("category_code"),
            "triage_complete": state.get("triage_complete"),
            "symptoms_summary": _trim_text(str(state.get("symptoms_summary") or ""), 300) or None,
            "last_agent_message": _trim_text(str(state.get("last_agent_message") or ""), 300) or None,
            "messages_count": len(messages),
            "last_human_message": last_human or None,
            "last_ai_message": last_ai,
        }
    elif node == "root_respond":
        available_slots = state.get("available_slots")
        node_state = {
            "intent": state.get("intent"),
            "current_agent": state.get("current_agent"),
            "skip_root_respond": state.get("skip_root_respond"),
            "category_code": state.get("category_code"),
            "available_slots_count": len(available_slots) if isinstance(available_slots, list) else None,
            "selected_slot": state.get("selected_slot"),
            "pending_confirmation_slot": state.get("pending_confirmation_slot"),
            "booking_confirmed": state.get("booking_confirmed"),
            "messages_count": len(messages),
            "last_human_message": last_human or None,
            "last_ai_message": last_ai,
        }
    else:
        # Fallback cho các node hệ thống/tool: giữ gọn, ổn định.
        node_state = {
            "intent": state.get("intent"),
            "current_agent": state.get("current_agent"),
            "category_code": state.get("category_code"),
            "triage_complete": state.get("triage_complete"),
            "booking_confirmed": state.get("booking_confirmed"),
            "reservation_id": state.get("reservation_id"),
        }

    return {
        "node": node,
        "turn_id": turn_id,
        "session_id": session_id,
        "patient_user_id": patient_user_id,
        "user_message_preview": _trim_text(user_message, 300),
        "llm_prompt_preview": _trim_text(llm_prompt, 800) if llm_prompt else "",
        "state": node_state,
    }


def _normalize_node_output_payload(*, node: str, turn_id: str, raw_output: object, llm_out: str) -> dict:
    out = raw_output if isinstance(raw_output, dict) else {}
    if node == "classify_intent":
        result = {
            "intent": out.get("intent"),
            "current_agent": out.get("current_agent"),
            "skip_root_respond": out.get("skip_root_respond"),
            "pending_confirmation_slot": out.get("pending_confirmation_slot"),
        }
    elif node == "dental_specialist":
        result = {
            "specialist_concluded": out.get("specialist_concluded"),
            "follow_up_count": out.get("follow_up_count"),
            "category_code": out.get("category_code"),
            "triage_complete": out.get("triage_complete"),
            "symptoms_summary": _trim_text(str(out.get("symptoms_summary") or ""), 300) or None,
            "ai_diagnosis": _trim_text(str(out.get("ai_diagnosis") or ""), 300) or None,
            "last_agent_message": _trim_text(str(out.get("last_agent_message") or ""), 300) or None,
        }
    elif node == "root_respond":
        available_slots = out.get("available_slots")
        result = {
            "current_agent": out.get("current_agent"),
            "skip_root_respond": out.get("skip_root_respond"),
            "available_slots_count": len(available_slots) if isinstance(available_slots, list) else None,
            "selected_slot": out.get("selected_slot"),
            "pending_confirmation_slot": out.get("pending_confirmation_slot"),
            "booking_confirmed": out.get("booking_confirmed"),
            "last_agent_message": _trim_text(str(out.get("last_agent_message") or ""), 300) or None,
            "has_message_ui": bool((out.get("extra") or {}).get("message_ui")) if isinstance(out.get("extra"), dict) else None,
        }
    else:
        result = {
            "intent": out.get("intent"),
            "category_code": out.get("category_code"),
            "triage_complete": out.get("triage_complete"),
            "intake_id": out.get("intake_id"),
            "booking_confirmed": out.get("booking_confirmed"),
            "reservation_id": out.get("reservation_id"),
        }
    return {
        "node": node,
        "turn_id": turn_id,
        "status": "success",
        "llm_response_preview": _trim_text(llm_out, 800) if llm_out else "",
        "result": result,
    }


def _reply_from_final_state(vals: dict) -> tuple[str, str]:
    """
    When LangGraph/Ollama does not emit on_chat_model_stream, the UI still needs text.
    Recover from checkpoint state: last_agent_message or the latest AIMessage.
    Returns (text, agent) where agent is 'specialist' or 'root'.
    """
    last = vals.get("last_agent_message")
    if isinstance(last, str) and last.strip():
        agent = vals.get("current_agent") or "root"
        if agent not in ("specialist", "root"):
            agent = "root"
        return last.strip(), agent

    msgs = vals.get("messages") or []
    for msg in reversed(msgs):
        if getattr(msg, "type", None) != "ai":
            continue
        content = msg.content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            content = "".join(parts)
        elif not isinstance(content, str):
            content = str(content or "")
        if not content.strip():
            continue
        name = (getattr(msg, "name", None) or "").lower()
        if "specialist" in name:
            return content.strip(), "specialist"
        return content.strip(), "root"
    return "", "root"


# ── Auth dependency ────────────────────────────────────────────────────────────

async def get_current_user(
    token: str,
    db: AsyncSession,
):
    """Validate JWT and return the PatientUser."""
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = auth_service.decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    user = await auth_service.get_user_by_id(db, int(user_id))
    if user is None:
        raise credentials_exc
    return user


# ── Route: Create Session ──────────────────────────────────────────────────────

@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _extract_token(authorization)
    user = await get_current_user(token, db)
    session = await chat_service.create_session(db, user.id)
    return session


# ── Route: List Sessions ───────────────────────────────────────────────────────

@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _extract_token(authorization)
    user = await get_current_user(token, db)
    sessions = await chat_service.list_sessions(db, user.id)
    return sessions


# ── Route: Get Session ─────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}", response_model=SessionWithMessages)
async def get_session(
    session_id: int,
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _extract_token(authorization)
    user = await get_current_user(token, db)
    session = await chat_service.get_session_with_messages(db, session_id)
    if not session or session.patient_user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


# ── Route: Send Message (SSE streaming) ───────────────────────────────────────

@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    message: str = Form(...),
    authorization: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a user message and stream the agent's response as SSE.

    Multipart form fields:
      - message       : user text
      - authorization : Bearer <token>
    """
    token = _extract_token(authorization)
    user = await get_current_user(token, db)

    session = await chat_service.get_session(db, session_id, user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.status != SessionStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="Session is no longer active.")

    # ── Persist user message ───────────────────────────────────────────────
    await chat_service.save_message(
        db, session_id, SenderType.PATIENT_USER, message, None
    )
    turn_index = await chat_service.count_messages_by_sender(
        db=db,
        session_id=session_id,
        sender_type=SenderType.PATIENT_USER,
    )

    preview = (message[:120] + "…") if len(message) > 120 else message
    logger.info(
        "[chat] send_message session_id=%s patient_user_id=%s text_len=%s preview=%r",
        session_id,
        user.id,
        len(message),
        preview,
    )

    return StreamingResponse(
        _stream_agent_response(
            session_id=session_id,
            patient_user_id=user.id,
            user_message=message,
            turn_index=turn_index,
            db=db,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Route: Close Session ───────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/close", response_model=SessionResponse)
async def close_session(
    session_id: int,
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _extract_token(authorization)
    user = await get_current_user(token, db)
    session = await chat_service.get_session(db, session_id, user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    await chat_service.update_session_status(db, session_id, SessionStatus.COMPLETED)
    await db.refresh(session)
    return session


# ── Streaming Generator ────────────────────────────────────────────────────────

async def _stream_agent_response(
    session_id: int,
    patient_user_id: int,
    user_message: str,
    turn_index: int,
    db: AsyncSession,
):
    """
    Runs the LangGraph graph and yields SSE events:
      data: {"type": "status",  "message": "..."}
      data: {"type": "token",   "content": "..."}
      data: {"type": "done",    "session_id": ..., "agent": "...", "booking": {...}}
      data: {"type": "error",   "message": "..."}
    """
    from langchain_core.messages import HumanMessage
    from app.agents.graph import get_graph
    graph = await get_graph()
    thread_config = {"configurable": {"thread_id": str(session_id)}}

    # Only merge fields that must change this turn. Omit the rest so the checkpointer
    # keeps booking state (available_slots, intake_id, pending_booking_date_iso, …).
    input_state: dict = {
        "messages": [HumanMessage(content=user_message)],
        "session_id": session_id,
        "patient_user_id": patient_user_id,
    }

    full_response = ""
    current_agent = "root"
    booking_info = None
    intake_info = None
    event_counts: dict[str, int] = {}
    node_started_at: dict[str, float] = {}
    node_inputs: dict[str, object] = {}
    node_outputs: dict[str, object] = {}
    node_llm_prompts: dict[str, str] = {}
    node_llm_outputs: dict[str, str] = {}
    token_events = 0
    t_stream_start = time.monotonic()
    trace_id = build_session_trace_id(session_id)
    turn_id = f"{max(turn_index, 1):04d}"
    turn_prefix = f"08.chat.turn.{turn_id}"
    turn_span = None
    _debug_events_left = 120  # cap DEBUG lines per request
    ensure_session_trace(
        session_id=session_id,
        user_id=str(patient_user_id),
        input_payload={
            "user_message": user_message,
            "session_id": session_id,
            "patient_user_id": patient_user_id,
        },
        metadata={
            "flow": "chat-stream",
            "llm_provider": settings.LLM_PROVIDER,
            "status": "in_progress",
            "level": "info",
        },
        tags=["chat", "session", settings.LLM_PROVIDER],
    )
    turn_span = create_langfuse_span(
        trace_id=trace_id,
        session_id=str(session_id),
        user_id=str(patient_user_id),
        span_name=f"{turn_prefix}.request",
        input_payload={
            "turn_id": turn_id,
            "user_message": user_message,
            "session_id": session_id,
            "patient_user_id": patient_user_id,
        },
        metadata={"status": "in_progress", "level": "info"},
        tags=["chat", "turn", "request", f"turn-{turn_id}"],
    )
    turn_span_id = str(getattr(turn_span, "id", "") or "")

    try:
        logger.info(
            "[chat][graph] astream_events start session_id=%s thread_id=%s provider=%s",
            session_id,
            thread_config["configurable"]["thread_id"],
            settings.LLM_PROVIDER,
        )
        yield _sse({"type": "status", "message": "Đang xử lý..."})

        async for event in graph.astream_events(
            input_state,
            config=thread_config,
            version="v2",
        ):
            kind = event["event"]
            name = event.get("name", "") or ""
            event_counts[kind] = event_counts.get(kind, 0) + 1

            if logger.isEnabledFor(logging.DEBUG) and _debug_events_left > 0:
                _debug_events_left -= 1
                logger.debug(
                    "[chat][graph] evt kind=%s name=%r tags=%s",
                    kind,
                    name,
                    list((event.get("tags") or [])[:3]),
                )

            if kind == "on_chain_start" and name in _GRAPH_NODES:
                node_started_at[name] = time.monotonic()
                node_inputs[name] = event.get("data", {}).get("input")
                logger.info(
                    "[chat][graph] --> node START name=%s session_id=%s",
                    name,
                    session_id,
                )

            if kind == "on_chain_end" and name in _GRAPH_NODES:
                node_end_t = time.monotonic()
                node_start_t = node_started_at.get(name)
                node_outputs[name] = event.get("data", {}).get("output")
                if node_start_t is not None:
                    llm_prompt = node_llm_prompts.get(name, "")
                    llm_out = node_llm_outputs.get(name, "")
                    emit_langfuse_system_span(
                        span_name=f"{turn_prefix}.graph.{name}",
                        session_id=str(session_id),
                        user_id=str(patient_user_id),
                        trace_id=trace_id,
                        started_at_monotonic=node_start_t,
                        ended_at_monotonic=node_end_t,
                        input_payload=_normalize_node_input_payload(
                            node=name,
                            turn_id=turn_id,
                            session_id=session_id,
                            patient_user_id=patient_user_id,
                            raw_state=node_inputs.get(name),
                            user_message=user_message,
                            llm_prompt=llm_prompt,
                        ),
                        output_payload=_normalize_node_output_payload(
                            node=name,
                            turn_id=turn_id,
                            raw_output=node_outputs.get(name),
                            llm_out=llm_out,
                        ),
                        metadata={
                            "scope": "langgraph-node",
                            "event": "on_chain_end",
                            "status": "success",
                            "level": "info",
                            "has_llm_prompt": bool(llm_prompt),
                            "has_llm_response": bool(llm_out),
                        },
                        tags=["chat", "graph-node", name, f"turn-{turn_id}"],
                        parent_observation_id=turn_span_id or None,
                    )
                logger.info(
                    "[chat][graph] <-- node END name=%s session_id=%s",
                    name,
                    session_id,
                )

            # ── Status updates when a node starts ──────────────────────────
            if kind == "on_chain_start":
                if name == "dental_specialist":
                    yield _sse({"type": "status", "message": "Chuyên gia nha khoa đang phân tích..."})
                elif name == "save_intake":
                    yield _sse({"type": "status", "message": "Đang lưu kết quả tư vấn..."})
                elif name == "query_slots":
                    yield _sse({"type": "status", "message": "Đang kiểm tra lịch trống..."})
                elif name == "confirm_booking":
                    yield _sse({"type": "status", "message": "Đang đặt lịch hẹn..."})

            # ── Stream LLM tokens ──────────────────────────────────────────
            elif kind == "on_chat_model_start":
                metadata = event.get("metadata", {}) or {}
                node = metadata.get("langgraph_node", "") or ""
                prompt_preview = _extract_prompt_preview(event.get("data", {}).get("input"))
                if node in _GRAPH_NODES and prompt_preview:
                    node_llm_prompts[node] = prompt_preview
                    logger.info(
                        "[chat][llm] on_chat_model_start session_id=%s node=%r prompt_preview=%r",
                        session_id,
                        node,
                        _trim_text(prompt_preview, 300),
                    )

            elif kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and chunk.content:
                    metadata = event.get("metadata", {}) or {}
                    node = metadata.get("langgraph_node", "")
                    # classify_intent chỉ phân loại nội bộ — không stream ra client (tránh full_response
                    # bị nhồi nhãn kiểu "select_slot" và bỏ qua fallback lấy AIMessage từ confirm_booking).
                    if node == "classify_intent":
                        continue
                    token_events += 1
                    token_text = (
                        chunk.content
                        if isinstance(chunk.content, str)
                        else str(chunk.content)
                    )
                    full_response += token_text
                    yield _sse({"type": "token", "content": token_text})

                    # Detect which agent is producing tokens
                    if node == "dental_specialist":
                        current_agent = "specialist"
                    elif node == "root_respond":
                        current_agent = "root"

            elif kind == "on_chat_model_end":
                output = event.get("data", {}).get("output")
                metadata = event.get("metadata", {}) or {}
                node = metadata.get("langgraph_node", "") or ""
                out_preview = ""
                if output is not None:
                    content = getattr(output, "content", None)
                    out_preview = _trim_text(_content_to_text(content), 500)
                    if not out_preview:
                        out_preview = str(type(output))
                if node in _GRAPH_NODES and out_preview:
                    node_llm_outputs[node] = out_preview
                logger.info(
                    "[chat][llm] on_chat_model_end session_id=%s name=%r node=%r output_preview=%r",
                    session_id,
                    name,
                    node,
                    out_preview,
                )

            elif kind == "on_chain_error":
                err = event.get("data", {}).get("error")
                logger.error(
                    "[chat][graph] on_chain_error session_id=%s name=%r error=%r",
                    session_id,
                    name,
                    err,
                )

        stream_elapsed = time.monotonic() - t_stream_start
        emit_langfuse_system_span(
            span_name=f"{turn_prefix}.stream",
            session_id=str(session_id),
            user_id=str(patient_user_id),
            trace_id=trace_id,
            started_at_monotonic=t_stream_start,
            ended_at_monotonic=time.monotonic(),
            input_payload={
                "user_message": user_message,
                "session_id": session_id,
            },
            output_payload={
                "token_events": token_events,
                "event_counts": dict(sorted(event_counts.items(), key=lambda x: -x[1])[:15]),
            },
            metadata={
                "scope": "chat-api",
                "token_events": token_events,
                "event_kinds_seen": len(event_counts),
                "status": "success",
                "level": "info",
            },
            tags=["chat", "stream", "turn", f"turn-{turn_id}"],
            parent_observation_id=turn_span_id or None,
        )

        # ── Final state (always) ────────────────────────────────────────────
        final_state = await graph.aget_state(thread_config)
        vals = final_state.values if hasattr(final_state, "values") else {}

        # Ollama + ChatOllama often emit on_chain_stream but NOT on_chat_model_stream,
        # so token-by-token SSE stays empty. Recover text from checkpoint and emit chunks.
        if not full_response.strip():
            fb_text, fb_agent = _reply_from_final_state(vals)
            if fb_text:
                full_response = fb_text
                current_agent = fb_agent
                logger.info(
                    "[chat][graph] reply from final_state (Ollama stream workaround) "
                    "session_id=%s len=%s agent=%s",
                    session_id,
                    len(full_response),
                    current_agent,
                )
                step = 48
                for i in range(0, len(full_response), step):
                    yield _sse({"type": "token", "content": full_response[i : i + step]})
                    token_events += 1

        logger.info(
            "[chat][graph] astream_events finished session_id=%s elapsed_s=%.2f "
            "token_stream_events=%s aggregated_text_len=%s event_counts=%s",
            session_id,
            stream_elapsed,
            token_events,
            len(full_response),
            dict(sorted(event_counts.items(), key=lambda x: -x[1])[:15]),
        )

        if not full_response.strip():
            logger.warning(
                "[chat][graph] EMPTY reply session_id=%s — no stream tokens and "
                "final_state had no assistant text.",
                session_id,
            )

        logger.info(
            "[chat][graph] final_state session_id=%s intent=%r specialist_concluded=%s "
            "intake_id=%s booking_confirmed=%s reservation_id=%s follow_up_count=%s",
            session_id,
            vals.get("intent"),
            vals.get("specialist_concluded"),
            vals.get("intake_id"),
            vals.get("booking_confirmed"),
            vals.get("reservation_id"),
            vals.get("follow_up_count"),
        )

        if vals.get("booking_confirmed"):
            booking_info = {
                "reservation_id": vals.get("reservation_id"),
                "selected_slot": vals.get("selected_slot"),
            }

        if vals.get("intake_id"):
            intake_info = {
                "intake_id": vals.get("intake_id"),
                "ai_diagnosis": vals.get("ai_diagnosis"),
                "category_code": vals.get("category_code"),
            }

        # ── Persist agent message to DB ────────────────────────────────────
        if full_response:
            sender = (
                SenderType.SPECIALIST_AGENT
                if current_agent == "specialist"
                else SenderType.ROOT_AGENT
            )
            await chat_service.save_message(db, session_id, sender, full_response)

        # ── Mark session COMPLETED if booking confirmed ───────────────────
        if booking_info:
            await chat_service.update_session_status(
                db, session_id, SessionStatus.COMPLETED
            )

        logger.info(
            "[chat] SSE done session_id=%s agent=%s saved_reply_len=%s booking=%s",
            session_id,
            current_agent,
            len(full_response),
            bool(booking_info),
        )
        ui_payload = None
        ex = vals.get("extra")
        if isinstance(ex, dict):
            ui_payload = ex.get("message_ui")

        yield _sse({
            "type": "done",
            "session_id": session_id,
            "agent": current_agent,
            "booking": booking_info,
            "intake": intake_info,
            "ui": ui_payload,
        })
        update_session_trace(
            trace_id=trace_id,
            output_payload={
                "last_turn": {
                    "turn_id": turn_id,
                    "agent": current_agent,
                    "full_response": full_response,
                    "booking": booking_info,
                    "intake": intake_info,
                    "token_events": token_events,
                }
            },
            metadata={
                "status": "success",
                "level": "info",
                "event_counts": dict(sorted(event_counts.items(), key=lambda x: -x[1])[:15]),
            },
            tags=["chat", "session", "success"],
        )
        end_langfuse_span(
            turn_span,
            output_payload={
                "turn_id": turn_id,
                "agent": current_agent,
                "reply": full_response,
                "token_events": token_events,
                "booking": booking_info,
            },
            metadata={"status": "success", "level": "info"},
        )

    except Exception as exc:
        logger.exception(
            "[chat][graph] stream FAILED session_id=%s after_s=%.2f error=%s",
            session_id,
            time.monotonic() - t_stream_start,
            exc,
        )
        update_session_trace(
            trace_id=trace_id,
            output_payload={
                "last_turn": {
                    "turn_id": turn_id,
                    "error": str(exc),
                    "session_id": session_id,
                }
            },
            metadata={"status": "error", "level": "error"},
            tags=["chat", "session", "error"],
        )
        emit_langfuse_system_span(
            span_name=f"{turn_prefix}.error",
            session_id=str(session_id),
            user_id=str(patient_user_id),
            trace_id=trace_id,
            started_at_monotonic=t_stream_start,
            ended_at_monotonic=time.monotonic(),
            input_payload={"user_message": user_message, "session_id": session_id},
            output_payload={"error": str(exc)},
            metadata={"scope": "chat-api", "status": "error", "level": "error"},
            tags=["chat", "error", f"turn-{turn_id}"],
            parent_observation_id=turn_span_id or None,
        )
        end_langfuse_span(
            turn_span,
            output_payload={"turn_id": turn_id, "error": str(exc)},
            metadata={"status": "error", "level": "error"},
        )
        yield _sse({"type": "error", "message": str(exc)})


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing.")
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return authorization
