"""
LangGraph Stateful Multi-Agent Graph
=====================================

Node flow (per user message turn):
───────────────────────────────────
  START
    │
    ▼
  classify_intent          ← Root Orchestrator (LLM phân intent)
    │
    ├─► "consultation"
    │      ▼
    │   dental_specialist  ← Thu thập triệu chứng, phân loại category_code (CAT-01→05)
    │      ├─► specialist_concluded=False → END  (hỏi thêm triệu chứng)
    │      └─► specialist_concluded=True
    │             ▼
    │          save_intake  ← Ghi BookingConsultIntake vào PostgreSQL
    │             ▼
    │          query_slots  ← Đọc lịch trống mock theo category
    │             ▼
    │          root_respond ← Soạn tin nhắn "chọn giờ" cho BN
    │             ▼
    │           END
    │
    ├─► "select_slot"
    │      ├─► chưa triage → dental_specialist
    │      ├─► đủ intake + slots → confirm_booking
    │      └─► thiếu → booking_prepare → confirm_booking
    │
    ├─► "confirm_appointment"
    │      └─► confirm_booking → (skip_root_respond ? END : root_respond → END)
    │
    └─► "general"
           └─► root_respond → END

State persistence: Redis (langgraph-checkpoint-redis).
  - Dev: redis-stack-server container trong Docker Compose.
  - Config: REDIS_URL trong .env.
  - Fallback: MemorySaver nếu Redis không kết nối được.
"""

import asyncio
import logging

from langgraph.graph import END, START, StateGraph

from app.agents.state import AgentState
from app.agents.root_orchestrator import (
    booking_prepare_node,
    classify_intent_node,
    confirm_booking_node,
    query_slots_node,
    root_respond_node,
    save_intake_node,
)
from app.agents.dental_specialist import dental_specialist_node

logger = logging.getLogger(__name__)


def _route_after_intent(state: AgentState) -> str:
    intent = state.get("intent", "general")
    sid = state.get("session_id")
    if intent == "consultation":
        logger.info("[graph][route] after classify_intent session_id=%s -> dental_specialist", sid)
        return "dental_specialist"
    elif intent == "confirm_appointment":
        logger.info("[graph][route] after classify_intent session_id=%s -> confirm_booking (confirm_appointment)", sid)
        return "confirm_booking"
    elif intent == "select_slot":
        if not state.get("triage_complete"):
            logger.info(
                "[graph][route] after classify_intent session_id=%s -> dental_specialist "
                "(select_slot without triage)",
                sid,
            )
            return "dental_specialist"
        has_prereqs = bool(state.get("intake_id")) and bool(state.get("available_slots"))
        if has_prereqs:
            logger.info("[graph][route] after classify_intent session_id=%s -> confirm_booking", sid)
            return "confirm_booking"
        logger.info(
            "[graph][route] after classify_intent session_id=%s -> booking_prepare "
            "(select_slot but missing intake_id or available_slots)",
            sid,
        )
        return "booking_prepare"
    logger.info("[graph][route] after classify_intent session_id=%s -> root_respond (intent=%r)", sid, intent)
    return "root_respond"


def _route_after_specialist(state: AgentState) -> str:
    sid = state.get("session_id")
    if state.get("specialist_concluded", False):
        logger.info("[graph][route] after dental_specialist session_id=%s -> save_intake (concluded)", sid)
        return "save_intake"
    logger.info(
        "[graph][route] after dental_specialist session_id=%s -> END (still collecting symptoms)",
        sid,
    )
    return END


def _route_after_confirm(state: AgentState):
    if state.get("skip_root_respond"):
        logger.info("[graph][route] after confirm_booking session_id=%s -> END (skip_root_respond)", state.get("session_id"))
        return END
    return "root_respond"


def _build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node("dental_specialist", dental_specialist_node)
    builder.add_node("save_intake", save_intake_node)
    builder.add_node("query_slots", query_slots_node)
    builder.add_node("booking_prepare", booking_prepare_node)
    builder.add_node("confirm_booking", confirm_booking_node)
    builder.add_node("root_respond", root_respond_node)

    builder.add_edge(START, "classify_intent")

    builder.add_conditional_edges(
        "classify_intent",
        _route_after_intent,
        {
            "dental_specialist": "dental_specialist",
            "confirm_booking": "confirm_booking",
            "booking_prepare": "booking_prepare",
            "root_respond": "root_respond",
        },
    )

    builder.add_conditional_edges(
        "dental_specialist",
        _route_after_specialist,
        {
            "save_intake": "save_intake",
            END: END,
        },
    )

    builder.add_edge("save_intake", "query_slots")
    builder.add_edge("query_slots", "root_respond")

    builder.add_edge("booking_prepare", "confirm_booking")

    builder.add_conditional_edges(
        "confirm_booking",
        _route_after_confirm,
        {"root_respond": "root_respond", END: END},
    )

    builder.add_edge("root_respond", END)

    return builder


async def _create_checkpointer():
    """Tạo async Redis checkpointer; fallback MemorySaver nếu Redis không khả dụng."""
    from app.config import settings

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        checkpointer = AsyncRedisSaver(redis_url=settings.REDIS_URL)
        await checkpointer.asetup()
        logger.info("[graph] Async Redis checkpointer ready (%s)", settings.REDIS_URL)
        return checkpointer
    except Exception as exc:
        logger.warning(
            "[graph] Async Redis checkpointer failed (%s): %s — falling back to MemorySaver",
            settings.REDIS_URL,
            exc,
        )
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


_GRAPH = None
_GRAPH_LOCK = asyncio.Lock()


async def get_graph():
    """Build and compile the graph (async-safe singleton per process)."""
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH

    async with _GRAPH_LOCK:
        if _GRAPH is not None:
            return _GRAPH
        checkpointer = await _create_checkpointer()
        _GRAPH = _build_graph().compile(checkpointer=checkpointer)
        logger.info("LangGraph compiled successfully")
        return _GRAPH
