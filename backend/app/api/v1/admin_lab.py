"""
Admin / QA lab – gọi độc lập node LLM và tool (giai đoạn dev: không xác thực JWT).
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from pathlib import Path
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


class DatasetSaveBody(BaseModel):
    rows: list[dict] = Field(default_factory=list, description="Danh sách record dataset (JSON objects)")


def _eval_dataset_dir() -> Path:
    # backend/app/api/v1/admin_lab.py -> backend/evals/datasets
    return Path(__file__).resolve().parents[3] / "evals" / "datasets"


def _eval_dataset_path(name: str) -> Path:
    safe = (name or "").strip()
    if not safe.endswith(".jsonl"):
        safe = f"{safe}.jsonl"
    if "/" in safe or ".." in safe or safe.startswith("."):
        raise HTTPException(status_code=400, detail="Tên dataset không hợp lệ.")
    path = _eval_dataset_dir() / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy dataset: {safe}")
    return path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Dataset lỗi JSONL tại dòng: {line[:120]}") from exc
        if not isinstance(obj, dict):
            raise HTTPException(status_code=400, detail="Mỗi dòng dataset phải là object JSON.")
        rows.append(obj)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    content = "\n".join(lines) + ("\n" if lines else "")
    path.write_text(content, encoding="utf-8")


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    idx = int(round((q / 100.0) * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


@router.get("/mock-schedule-summary")
async def mock_schedule_summary():
    """Tóm tắt dữ liệu lịch mock trong JSON (để so sánh khi test tool/API)."""
    return mock_schedule_summary_for_lab()


@router.get("/triage-rubric")
async def triage_rubric_dump():
    """Toàn bộ rubric triệu chứng ↔ mã loại khám + 90 ví dụ (file mock JSON)."""
    return load_triage_rubric_raw()


@router.get("/benchmarks/datasets")
async def benchmark_dataset_list():
    root = _eval_dataset_dir()
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(root.glob("*.jsonl")):
        rows = _read_jsonl(p)
        items.append(
            {
                "name": p.name,
                "rows": len(rows),
            }
        )
    return {"datasets": items}


@router.get("/benchmarks/datasets/{dataset_name}")
async def benchmark_dataset_get(dataset_name: str):
    path = _eval_dataset_path(dataset_name)
    rows = _read_jsonl(path)
    return {"dataset": path.name, "rows": rows}


@router.put("/benchmarks/datasets/{dataset_name}")
async def benchmark_dataset_save(dataset_name: str, body: DatasetSaveBody):
    path = _eval_dataset_path(dataset_name)
    _write_jsonl(path, body.rows or [])
    return {"dataset": path.name, "saved_rows": len(body.rows or [])}


@router.post("/benchmarks/run")
async def benchmark_run():
    intent_rows = _read_jsonl(_eval_dataset_path("intent_routing.jsonl"))
    triage_rows = _read_jsonl(_eval_dataset_path("triage_quality.jsonl"))
    booking_rows = _read_jsonl(_eval_dataset_path("booking_success.jsonl"))

    # Intent benchmark
    intent_hit = 0
    intent_lat: list[float] = []
    intent_details: list[dict[str, Any]] = []
    for i, row in enumerate(intent_rows, start=1):
        state = _default_agent_state(
            session_id=50000 + i,
            patient_user_id=1,
            message=str(row.get("message") or ""),
            patch=dict(row.get("state_patch") or {}),
        )
        t0 = time.monotonic()
        updates = await classify_intent_node(state, {"configurable": {"thread_id": f"lab-bench-intent-{i}"}})
        elapsed = (time.monotonic() - t0) * 1000.0
        intent_lat.append(elapsed)
        pred = updates.get("intent")
        expected = row.get("expected_intent")
        ok = pred == expected
        if ok:
            intent_hit += 1
        intent_details.append(
            {
                "id": row.get("id", f"intent-{i}"),
                "expected_intent": expected,
                "predicted_intent": pred,
                "ok": ok,
                "latency_ms": round(elapsed, 2),
            }
        )

    # Triage benchmark
    triage_hit = 0
    triage_lat: list[float] = []
    triage_details: list[dict[str, Any]] = []
    for i, row in enumerate(triage_rows, start=1):
        messages = [{"role": "human", "content": m} for m in (row.get("messages") or [])]
        state = _default_agent_state(
            session_id=60000 + i,
            patient_user_id=1,
            message="",
            patch={
                "messages": messages,
                "follow_up_count": 99,  # force conclude this turn
                "symptoms_summary": " ".join(str(m) for m in (row.get("messages") or [])),
            },
        )
        t0 = time.monotonic()
        updates = await dental_specialist_node(state, {"configurable": {"thread_id": f"lab-bench-triage-{i}"}})
        elapsed = (time.monotonic() - t0) * 1000.0
        triage_lat.append(elapsed)
        pred = updates.get("category_code")
        expected = row.get("expected_category_code")
        ok = pred == expected
        if ok:
            triage_hit += 1
        triage_details.append(
            {
                "id": row.get("id", f"triage-{i}"),
                "expected_category_code": expected,
                "predicted_category_code": pred,
                "ok": ok,
                "latency_ms": round(elapsed, 2),
            }
        )

    # Booking benchmark
    booking_ok = 0
    booking_lat: list[float] = []
    booking_details: list[dict[str, Any]] = []
    for i, row in enumerate(booking_rows, start=1):
        intake_json = await save_consult_intake.ainvoke(
            {
                "patient_user_id": 1,
                "session_id": 70000 + i,
                "symptoms": "Benchmark synthetic symptoms",
                "ai_diagnosis": "Benchmark only",
                "needs_visit": True,
                "category_code": row.get("category_code"),
            }
        )
        intake = json.loads(intake_json)
        intake_id = intake.get("intake_id")
        slots_json = await get_mock_schedule.ainvoke(
            {
                "scope": "day",
                "date_str": row.get("date_str"),
                "category_code": row.get("category_code"),
            }
        )
        slots_data = json.loads(slots_json)
        slots = slots_data.get("slots") or []
        if not intake_id or not slots:
            booking_details.append(
                {
                    "id": row.get("id", f"booking-{i}"),
                    "ok": False,
                    "reason": "missing intake or slots",
                }
            )
            continue
        t0 = time.monotonic()
        book_json = await book_appointment.ainvoke(
            {
                "patient_user_id": 1,
                "intake_id": intake_id,
                "datetime_str": slots[0].get("datetime_str"),
            }
        )
        elapsed = (time.monotonic() - t0) * 1000.0
        booking_lat.append(elapsed)
        book_out = json.loads(book_json)
        ok = bool(book_out.get("reservation_id"))
        if ok:
            booking_ok += 1
        booking_details.append(
            {
                "id": row.get("id", f"booking-{i}"),
                "reservation_id": book_out.get("reservation_id"),
                "ok": ok,
                "latency_ms": round(elapsed, 2),
            }
        )

    def _metric(total: int, good: int, lat: list[float], details: list[dict[str, Any]], rate_key: str):
        denom = max(total, 1)
        return {
            "total": total,
            ("correct" if rate_key == "accuracy" else "success"): good,
            rate_key: round(good / denom, 4),
            "latency_ms": {
                "avg": round(statistics.mean(lat), 2) if lat else 0.0,
                "p50": round(_pct(lat, 50), 2),
                "p95": round(_pct(lat, 95), 2),
            },
            "details": details,
        }

    return {
        "generated_at_unix": int(time.time()),
        "benchmarks": {
            "intent_routing_accuracy": _metric(len(intent_rows), intent_hit, intent_lat, intent_details, "accuracy"),
            "triage_quality_accuracy": _metric(len(triage_rows), triage_hit, triage_lat, triage_details, "accuracy"),
            "booking_success_rate": _metric(len(booking_rows), booking_ok, booking_lat, booking_details, "success_rate"),
        },
    }


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
