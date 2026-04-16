"""
Admin / QA lab – gọi độc lập node LLM và tool (giai đoạn dev: không xác thực JWT).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.agents.root_orchestrator import classify_intent_node, root_respond_node
from app.agents.dental_specialist import dental_specialist_node
from app.agents.state import AgentState
from app.services.mock_week_schedule_loader import mock_schedule_summary_for_lab
from app.services.triage_rubric_loader import load_triage_rubric_raw
from app.tools.intake_tools import save_consult_intake
from app.tools.schedule_tools import (
    book_appointment,
    get_mock_schedule,
    infer_date_str_from_user_text,
    resolve_requested_slot,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize_lab_value(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_serialize_lab_value(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize_lab_value(v) for k, v in obj.items()}
    t = getattr(obj, "type", None)
    if t in ("human", "ai", "system", "tool"):
        content = getattr(obj, "content", "")
        if not isinstance(content, str):
            content = str(content)
        return {
            "type": t,
            "content": content,
            "name": getattr(obj, "name", None),
        }
    return str(obj)


def _messages_from_patch(raw: list[dict]) -> list:
    out = []
    for m in raw:
        role = (m.get("role") or "human").lower()
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        if role == "human":
            out.append(HumanMessage(content=content))
        elif role in ("ai", "assistant"):
            out.append(AIMessage(content=content, name=m.get("name") or "assistant"))
        else:
            out.append(HumanMessage(content=content))
    return out


def _default_agent_state(
    session_id: int,
    patient_user_id: int,
    message: str,
    patch: dict,
) -> AgentState:
    messages = patch.pop("messages", None)
    if messages is not None:
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="state_patch.messages phải là mảng.")
        lc_messages = _messages_from_patch(messages)
    else:
        lc_messages = [HumanMessage(content=message or "")]

    base: dict[str, Any] = {
        "session_id": session_id,
        "patient_user_id": patient_user_id,
        "messages": lc_messages,
        "intent": "general",
        "current_agent": "root",
        "symptoms_summary": None,
        "ai_diagnosis": None,
        "follow_up_count": 0,
        "specialist_concluded": False,
        "pending_category_confirmation": False,
        "category_code": None,
        "triage_complete": None,
        "intake_id": None,
        "available_slots": [],
        "selected_slot": None,
        "booking_confirmed": False,
        "reservation_id": None,
        "pending_booking_date_iso": None,
        "pending_confirmation_slot": None,
        "skip_root_respond": False,
        "last_agent_message": None,
        "extra": None,
    }
    for key, val in patch.items():
        base[key] = val
    base["session_id"] = session_id
    base["patient_user_id"] = patient_user_id
    base["messages"] = lc_messages
    return base  # type: ignore[return-value]


AGENT_NODES = {
    "classify_intent": classify_intent_node,
    "dental_specialist": dental_specialist_node,
    "root_respond": root_respond_node,
}

TOOL_REGISTRY: dict[str, Any] = {
    "get_mock_schedule": get_mock_schedule,
    "book_appointment": book_appointment,
    "save_consult_intake": save_consult_intake,
}


class AgentInvokeBody(BaseModel):
    agent: str = Field(..., description="classify_intent | dental_specialist | root_respond")
    message: str = Field("", description="Dùng khi không gửi state_patch.messages")
    session_id: int = Field(1, ge=1)
    patient_user_id: int = Field(1, ge=1)
    state_patch: dict = Field(default_factory=dict, description="Ghi đè state; messages: [{role, content}]")


class ToolInvokeBody(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)


@router.get("/mock-schedule-summary")
async def mock_schedule_summary():
    """Tóm tắt dữ liệu lịch mock trong JSON (để so sánh khi test tool/API)."""
    return mock_schedule_summary_for_lab()


@router.get("/triage-rubric")
async def triage_rubric_dump():
    """Toàn bộ rubric triệu chứng ↔ mã loại khám + 90 ví dụ (file mock JSON)."""
    return load_triage_rubric_raw()


@router.get("/sessions/{session_id}/state")
async def session_state(session_id: int):
    """
    Lấy state hiện tại của session từ LangGraph checkpointer (thread_id=session_id).
    """
    from app.agents.graph import get_graph

    graph = await get_graph()
    config: RunnableConfig = {"configurable": {"thread_id": str(session_id)}}
    try:
        snap = await graph.aget_state(config)
    except Exception as e:
        logger.exception("[admin_lab] session_state failed session_id=%s", session_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    values = {}
    metadata = {}
    next_nodes: list[str] = []
    if snap is not None:
        values = _serialize_lab_value(getattr(snap, "values", {}) or {})
        metadata = _serialize_lab_value(getattr(snap, "metadata", {}) or {})
        next_raw = getattr(snap, "next", ()) or ()
        next_nodes = [str(x) for x in next_raw]

    return {
        "session_id": session_id,
        "has_checkpoint": bool(snap),
        "next_nodes": next_nodes,
        "state": values,
        "metadata": metadata,
    }


@router.post("/agents/invoke")
async def invoke_agent(body: AgentInvokeBody):
    node_fn = AGENT_NODES.get(body.agent)
    if not node_fn:
        raise HTTPException(
            status_code=400,
            detail=f"Agent không hợp lệ. Có: {', '.join(sorted(AGENT_NODES))}",
        )
    patch = dict(body.state_patch or {})

    state = _default_agent_state(
        body.session_id,
        body.patient_user_id,
        body.message,
        patch,
    )
    config: RunnableConfig = {"configurable": {"thread_id": f"lab-agent-{body.session_id}"}}
    try:
        updates = await node_fn(state, config)
    except Exception as e:
        logger.exception("[admin_lab] agent %s failed", body.agent)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "agent": body.agent,
        "updates": _serialize_lab_value(updates),
    }


@router.post("/tools/invoke")
async def invoke_tool(body: ToolInvokeBody):
    name = body.tool
    args = dict(body.args or {})

    if name == "resolve_requested_slot":
        try:
            out = resolve_requested_slot(
                date_iso=str(args.get("date_iso", "")),
                hour=int(args.get("hour", 0)),
                minute=int(args.get("minute", 0)),
                category_code=args.get("category_code"),
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"tool": name, "result": _serialize_lab_value(out)}

    if name == "infer_date_str_from_user_text":
        text = args.get("user_text") or args.get("text") or ""
        out = infer_date_str_from_user_text(str(text))
        return {"tool": name, "result": out}

    lc_tool = TOOL_REGISTRY.get(name)
    if not lc_tool:
        raise HTTPException(
            status_code=400,
            detail=f"Tool không hợp lệ. Có: {', '.join(sorted(TOOL_REGISTRY))} "
            "+ resolve_requested_slot, infer_date_str_from_user_text",
        )

    try:
        raw = await lc_tool.ainvoke(args)
    except Exception as e:
        logger.exception("[admin_lab] tool %s failed", name)
        raise HTTPException(status_code=500, detail=str(e)) from e

    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw

    return {"tool": name, "result": _serialize_lab_value(parsed)}
