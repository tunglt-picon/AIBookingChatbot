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
from app.schemas.chat import MessageResponse, SessionResponse, SessionWithMessages
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
    graph = get_graph()
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
    token_events = 0
    t_stream_start = time.monotonic()
    _debug_events_left = 120  # cap DEBUG lines per request

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
                logger.info(
                    "[chat][graph] --> node START name=%s session_id=%s",
                    name,
                    session_id,
                )

            if kind == "on_chain_end" and name in _GRAPH_NODES:
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
                out_preview = ""
                if output is not None:
                    content = getattr(output, "content", None)
                    if isinstance(content, str):
                        out_preview = (content[:200] + "…") if len(content) > 200 else content
                    else:
                        out_preview = str(type(output))
                logger.info(
                    "[chat][llm] on_chat_model_end session_id=%s name=%r output_preview=%r",
                    session_id,
                    name,
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

    except Exception as exc:
        logger.exception(
            "[chat][graph] stream FAILED session_id=%s after_s=%.2f error=%s",
            session_id,
            time.monotonic() - t_stream_start,
            exc,
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
