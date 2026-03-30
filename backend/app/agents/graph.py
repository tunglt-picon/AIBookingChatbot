"""
LangGraph Stateful Multi-Agent Graph
=====================================

Node flow (per user message turn):
───────────────────────────────────
  START
    │
    ▼
  classify_intent          ← Root Orchestrator
    │
    ├─► "consultation"
    │      │
    │      ▼
    │   dental_specialist  ← Dental Specialist (VLM)
    │      │
    │      ├─► needs_visit=False → END  (return follow-up question to user)
    │      │
    │      └─► needs_visit=True
    │             │
    │             ▼
    │          save_intake  ← persist BookingConsultIntake to DB
    │             │
    │             ▼
    │          query_slots  ← mock schedule service
    │             │
    │             ▼
    │          root_respond ← compose "here are your slots" message
    │             │
    │             ▼
    │           END
    │
    ├─► "select_slot"
    │      │
    │      ├─► (no intake or no slots yet)
    │      │      ▼
    │      │   booking_prepare ← walk-in intake + get_available_slots
    │      │      │
    │      │      ▼
    │      └────► confirm_booking  ← parse chosen slot, write Reservation to DB
    │      │
    │      ▼
    │   root_respond        ← booking confirmation message
    │      │
    │      ▼
    │    END
    │
    └─► "general"
           │
           ▼
        root_respond
           │
           ▼
          END

State persistence: MemorySaver (in-process).
Production: swap for AsyncPostgresSaver / AsyncRedisSaver.
"""

import logging
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
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
    """If specialist decided visit is needed → persist + schedule; else wait for user."""
    sid = state.get("session_id")
    if state.get("needs_visit", False):
        logger.info("[graph][route] after dental_specialist session_id=%s -> save_intake", sid)
        return "save_intake"
    logger.info(
        "[graph][route] after dental_specialist session_id=%s -> END (needs_visit=False, wait user)",
        sid,
    )
    return END


def _route_after_confirm(state: AgentState):
    """If confirm_booking already sent the reply, skip root_respond (no second LLM)."""
    if state.get("skip_root_respond"):
        logger.info("[graph][route] after confirm_booking session_id=%s -> END (skip_root_respond)", state.get("session_id"))
        return END
    return "root_respond"


def _build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    # ── Register nodes ────────────────────────────────────────────────────────
    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node("dental_specialist", dental_specialist_node)
    builder.add_node("save_intake", save_intake_node)
    builder.add_node("query_slots", query_slots_node)
    builder.add_node("booking_prepare", booking_prepare_node)
    builder.add_node("confirm_booking", confirm_booking_node)
    builder.add_node("root_respond", root_respond_node)

    # ── Edges ─────────────────────────────────────────────────────────────────
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


@lru_cache(maxsize=1)
def get_graph():
    """Build and compile the graph (singleton per process)."""
    checkpointer = MemorySaver()
    graph = _build_graph().compile(checkpointer=checkpointer)
    logger.info("LangGraph compiled successfully")
    return graph
