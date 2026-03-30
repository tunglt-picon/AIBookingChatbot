"""
LangGraph shared state definition.

Every field that uses `Annotated[list, add_messages]` is automatically merged
(appended) by LangGraph when state updates are returned from a node.
All other fields are overwritten on each update.
"""

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # ── Identity ─────────────────────────────────
    session_id: int
    patient_user_id: int

    # ── Conversation history ──────────────────────
    # add_messages reducer: new messages are appended, never overwritten
    messages: Annotated[list[AnyMessage], add_messages]

    # ── Routing ───────────────────────────────────
    # "consultation" | "select_slot" | "general" | "booking_complete"
    intent: str
    current_agent: str  # "root" | "specialist"

    # ── Image (multimodal) ────────────────────────
    image_base64: Optional[str]
    image_mime_type: Optional[str]   # e.g. "image/jpeg"

    # ── Clinical assessment ───────────────────────
    symptoms_summary: Optional[str]   # accumulated by specialist
    ai_diagnosis: Optional[str]
    needs_visit: bool
    follow_up_count: int              # merging control: max 3 follow-ups

    # ── Booking flow ─────────────────────────────
    intake_id: Optional[int]          # FK to booking_consult_intakes
    available_slots: list[dict]       # [{datetime_str, display}, ...]
    selected_slot: Optional[str]      # ISO datetime string chosen by user
    booking_confirmed: bool
    reservation_id: Optional[int]
    # ISO date (YYYY-MM-DD) for the slot list; used when user sends only a time later
    pending_booking_date_iso: Optional[str]
    # After availability check, wait for explicit confirmation before DB write
    pending_confirmation_slot: Optional[dict]  # {datetime_str, display}
    # When True, graph ends after confirm_booking (user-facing text already emitted there)
    skip_root_respond: bool

    # ── Streaming metadata (not persisted) ────────
    last_agent_message: Optional[str]
    extra: Optional[dict[str, Any]]   # open-ended scratch-pad for nodes
