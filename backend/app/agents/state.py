"""
LangGraph shared state definition.

Every field that uses `Annotated[list, add_messages]` is automatically merged
(appended) by LangGraph when state updates are returned from a node.
All other fields are overwritten on each update.

State được persist qua Redis checkpoint (xem graph.py).
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
    messages: Annotated[list[AnyMessage], add_messages]

    # ── Routing ───────────────────────────────────
    # "consultation" | "select_slot" | "general" | "confirm_appointment"
    intent: str
    current_agent: str  # "root" | "specialist"

    # ── Clinical assessment ───────────────────────
    symptoms_summary: Optional[str]
    ai_diagnosis: Optional[str]
    follow_up_count: int
    # CAT-01 | CAT-02 | CAT-03 | CAT-04 | CAT-05
    category_code: Optional[str]
    # Per-turn: True khi specialist chốt xong (có JSON / force_conclusion) → route đến save_intake
    specialist_concluded: bool
    # True khi đang chờ BN xác nhận category vừa phân loại
    pending_category_confirmation: bool
    # True khi BN bấm "Nhóm khác" và đang chờ chọn lại category từ danh sách 5 nhóm.
    pending_category_selection: Optional[bool]
    # True sau khi save_intake ghi DB — mới được chọn giờ
    triage_complete: Optional[bool]

    # ── Booking flow ─────────────────────────────
    intake_id: Optional[int]
    available_slots: list[dict]
    selected_slot: Optional[str]
    booking_confirmed: bool
    reservation_id: Optional[int]
    pending_booking_date_iso: Optional[str]
    pending_confirmation_slot: Optional[dict]
    skip_root_respond: bool

    # ── Streaming metadata (transient — overwritten mỗi lượt) ─
    last_agent_message: Optional[str]
    extra: Optional[dict[str, Any]]
