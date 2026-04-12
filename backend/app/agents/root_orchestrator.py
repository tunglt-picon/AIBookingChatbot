"""
Root orchestrator (text LLM / configurable).

Responsibilities:
  1. classify_intent_node – route user intent (consultation / slot selection / general).
  2. root_respond_node    – compose replies (FAQ, slot list, booking confirmation).
  3. save_intake_node     – persist BookingConsultIntake after the specialist path.
  4. booking_prepare_node – ensure intake + slots exist for direct booking flows.
  5. confirm_booking_node – match a slot and create a Reservation.
"""

import json
import logging
import re
import time
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.llm_factory import get_root_llm
from app.config import settings
from app.observability.langfuse_client import get_langfuse_callback

logger = logging.getLogger(__name__)

# Note: LLM system prompts below are Vietnamese for the default patient-facing UI locale.


def _date_iso_from_slot_datetime(datetime_str: str | None) -> str:
    """YYYY-MM-DD from slot ISO datetime (for FE inline reschedule)."""
    from datetime import datetime

    if not datetime_str or not isinstance(datetime_str, str):
        return ""
    try:
        normalized = datetime_str.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        return datetime_str[:10] if len(datetime_str) >= 10 else ""


def _last_human_text(state: AgentState) -> str:
    for msg in reversed(state.get("messages") or []):
        if hasattr(msg, "type") and msg.type == "human":
            return msg.content if isinstance(msg.content, str) else str(msg.content or "")
    return ""


def _looks_like_booking_confirmation(text: str) -> bool:
    low = text.lower().strip()
    if len(low) > 80:
        return False
    keys = (
        "đồng ý", "dong y", "xác nhận", "xac nhan", "ok", "chốt", "chot",
        "đặt luôn", "dat luon", "yes", "confirm", "nhất trí", "nhat tri",
        "đặt giúp", "dat giup", "giữ giúp", "giu giup",
    )
    return any(k in low for k in keys)


def _looks_like_booking_rejection(text: str) -> bool:
    low = text.lower().strip()
    if len(low) > 80:
        return False
    keys = (
        "không", "khong", "hủy", "huy", "thôi", "thoi", "chưa", "chua",
        "đổi giờ", "doi gio", "đổi lịch", "doi lich", "khác", "khac",
        "chọn lại", "chon lai", "lại giờ", "lai gio",
    )
    return any(k in low for k in keys)


def _parse_first_time_hm(user_text: str) -> tuple[int, int] | None:
    import re
    low = user_text.lower()
    m = re.search(r"\b(\d{1,2})\s*:\s*(\d{2})\b", low)
    if m:
        return int(m.group(1)), int(m.group(2))
    m2 = re.search(r"(?:^|\s)(\d{1,2})\s*h(?:\s|$|[^\d])", low)
    if m2:
        return int(m2.group(1)), 0
    return None


def _booking_date_iso_from_state(state: AgentState) -> str | None:
    iso = state.get("pending_booking_date_iso")
    if isinstance(iso, str) and iso.strip():
        return iso.strip()
    slots = state.get("available_slots") or []
    if slots:
        try:
            from datetime import datetime
            return datetime.fromisoformat(slots[0]["datetime_str"]).date().isoformat()
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
    return None


def _default_next_weekday_iso() -> str:
    from datetime import datetime, timedelta, timezone

    d = datetime.now(timezone.utc).date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def _looks_like_booking_intent(text: str) -> bool:
    """Bệnh nhân muốn hẹn giờ/ngày khám nhưng chưa mô tả triệu chứng (heuristic)."""
    low = text.lower().strip()
    if len(low) > 160:
        return False
    keys = (
        "đặt lịch", "dat lich", "book", "hẹn khám", "hen kham",
        "lịch khám", "lich kham", "đặt hẹn", "dat hen",
        "muốn khám", "muon kham", "chọn giờ", "chon gio",
    )
    if any(k in low for k in keys):
        return True
    if "ngày mai" in low or "ngay mai" in low:
        return True
    if re.search(r"thứ\s+[2-7]", low) or re.search(r"thu\s+[2-7]", low):
        return True
    return False


# ── Intent Classification ─────────────────────────────────────────────────────

_INTENT_SYSTEM = """\
Bạn là AI phân loại ý định của phòng khám nha khoa. Phân tích tin nhắn cuối cùng của bệnh nhân \
và phân loại thành MỘT trong các nhãn sau:

- consultation : bệnh nhân mô tả triệu chứng, vấn đề răng miệng, gửi ảnh răng, \
  hoặc đang tiếp tục cuộc trò chuyện tư vấn đang diễn ra
- select_slot  : bệnh nhân muốn đặt lịch, nêu ngày và/hoặc giờ khám, hoặc chọn khung giờ
- confirm_appointment : bệnh nhân đồng ý / OK / xác nhận khi bot vừa hỏi có đặt lịch khung giờ đó không
- general      : câu hỏi chung, lời chào, câu hỏi về phòng khám, hoặc chủ đề không liên quan

Ngữ cảnh hiện tại:
  - Cuộc tư vấn đang diễn ra: {has_consultation}
  - Đã hoàn tất tiếp nhận triệu chứng + phân loại (được phép chọn giờ): {triage_complete}
  - Đã hiển thị khung giờ: {has_slots}
  - Số câu hỏi làm rõ đã hỏi: {follow_up_count}/{max_follow_ups}
  - Đang chờ bệnh nhân xác nhận lịch đề xuất: {pending_confirm_context}

Quy tắc routing:
  - Nếu triage_complete = không/chưa, và bệnh nhân muốn đặt lịch hoặc nói ngày/giờ khám,
    ưu tiên **consultation** (cần mô tả triệu chứng với chuyên gia trước), trừ khi đang chờ xác nhận lịch.

Chỉ trả lời bằng MỘT nhãn: consultation | select_slot | confirm_appointment | general
"""


async def classify_intent_node(state: AgentState, config: RunnableConfig) -> dict:
    llm = get_root_llm()
    callbacks = get_langfuse_callback(config)

    last_human = _last_human_text(state)
    if state.get("pending_confirmation_slot") and _looks_like_booking_confirmation(last_human):
        logger.info(
            "[classify_intent] shortcut confirm_appointment session_id=%s",
            state["session_id"],
        )
        return {"intent": "confirm_appointment", "current_agent": "root", "skip_root_respond": False}
    if state.get("pending_confirmation_slot") and _looks_like_booking_rejection(last_human):
        logger.info(
            "[classify_intent] clear pending_confirmation (rejection) session_id=%s",
            state["session_id"],
        )
        return {
            "intent": "select_slot",
            "current_agent": "root",
            "pending_confirmation_slot": None,
            "skip_root_respond": False,
        }

    if not state.get("triage_complete"):
        if _looks_like_booking_intent(last_human):
            logger.info(
                "[classify_intent] booking-like message without triage -> consultation session_id=%s",
                state["session_id"],
            )
            return {"intent": "consultation", "current_agent": "root", "skip_root_respond": False}

    has_consultation = bool(state.get("symptoms_summary") or state.get("follow_up_count", 0) > 0)
    has_slots = bool(state.get("available_slots"))
    pending = state.get("pending_confirmation_slot")
    if pending and isinstance(pending, dict) and pending.get("display"):
        pending_confirm_context = f"có — {pending.get('display')}"
    else:
        pending_confirm_context = "không"

    triage_done = bool(state.get("triage_complete"))
    system_content = _INTENT_SYSTEM.format(
        has_consultation=has_consultation,
        triage_complete="có" if triage_done else "chưa",
        has_slots=has_slots,
        follow_up_count=state.get("follow_up_count", 0),
        max_follow_ups=settings.MAX_FOLLOW_UP_QUESTIONS,
        pending_confirm_context=pending_confirm_context,
    )

    # Only pass the last few messages to keep the prompt concise
    recent_messages = state["messages"][-6:]
    prompt = [SystemMessage(content=system_content)] + recent_messages

    logger.info(
        "[agent:root] classify_intent LLM invoke start session_id=%s msgs_in_prompt=%s "
        "(if this line stays last: Ollama is slow/unreachable — see GET /api/health/ollama "
        "and `curl %s/api/tags`)",
        state["session_id"],
        len(prompt),
        settings.OLLAMA_BASE_URL.rstrip("/"),
    )
    t0 = time.monotonic()
    response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    logger.info(
        "[agent:root] classify_intent LLM done session_id=%s elapsed_s=%.2f raw_preview=%r",
        state["session_id"],
        time.monotonic() - t0,
        (response.content[:200] + "…") if isinstance(response.content, str) and len(response.content) > 200 else response.content,
    )
    intent_raw = response.content.strip().lower()

    if "consultation" in intent_raw:
        intent = "consultation"
    elif "confirm_appointment" in intent_raw:
        intent = "confirm_appointment"
    elif "select_slot" in intent_raw:
        intent = "select_slot"
    else:
        intent = "general"

    logger.info(f"[classify_intent] session={state['session_id']} intent={intent!r}")
    return {"intent": intent, "current_agent": "root", "skip_root_respond": False}


# ── General Response ──────────────────────────────────────────────────────────

_ROOT_SYSTEM = """\
Bạn là trợ lý AI thân thiện của Phòng Khám Nha Khoa SmileCare. \
Nhiệm vụ của bạn là chào đón bệnh nhân, thu thập thông tin ban đầu và hướng dẫn họ \
qua quy trình đặt lịch hẹn.

Thông tin phòng khám:
  - Địa chỉ: 123 Nguyễn Huệ, TP.HCM
  - Giờ làm việc: Thứ 2–6, 08:00–17:00
  - Hotline: 028-1234-5678

Quy định bắt buộc:
  - KHÔNG đưa tư vấn y khoa, KHÔNG chẩn đoán bệnh, KHÔNG kê đơn.
  - Chỉ hỗ trợ đặt lịch, xác nhận thông tin hành chính và thu thập triệu chứng ban đầu.
  - Hệ thống sẽ **phân loại nhu cầu khám** (để xếp thời lượng & khung giờ phù hợp) sau khi bệnh nhân mô tả triệu chứng với chuyên gia tiếp nhận.
  - Nếu người dùng hỏi về điều trị/chẩn đoán, trả lời rằng hệ thống không tư vấn y khoa
    và mời đặt lịch khám trực tiếp.

Hãy trả lời ngắn gọn, thân thiện và bằng tiếng Việt (trừ khi bệnh nhân dùng tiếng Anh).
"""


async def root_respond_node(state: AgentState, config: RunnableConfig) -> dict:
    """General-purpose response: greetings, FAQs, slot presentation, booking confirmation."""
    llm = get_root_llm()
    callbacks = get_langfuse_callback(config)

    extra_context = ""
    if state.get("dental_case_code"):
        from app.domain.dental_cases import case_label_vi

        extra_context += (
            f"\nĐã phân loại nhu cầu khám (đặt lịch): **{case_label_vi(state['dental_case_code'])}**.\n"
            f"Mỗi loại có **thời lượng và khung giờ trống riêng** trong hệ thống.\n"
        )
    if state.get("available_slots"):
        slots_text = "\n".join(
            f"  • {s['display']}" for s in state["available_slots"]
        )
        extra_context += f"\nCác khung giờ trống hiện có:\n{slots_text}\n"
        extra_context += "\nHãy hỏi bệnh nhân muốn chọn khung giờ nào."

    if state.get("booking_confirmed") and state.get("reservation_id"):
        # Respond with booking confirmation details
        slot_display = state.get("selected_slot", "")
        extra_context += f"\nĐặt lịch thành công! Mã đặt lịch: #{state['reservation_id']}. Giờ hẹn: {slot_display}."

    system = _ROOT_SYSTEM + extra_context
    recent_messages = state["messages"][-10:]
    prompt = [SystemMessage(content=system)] + recent_messages

    logger.info(
        "[agent:root] root_respond LLM invoke start session_id=%s has_slots=%s booking_confirmed=%s",
        state["session_id"],
        bool(state.get("available_slots")),
        bool(state.get("booking_confirmed")),
    )
    t0 = time.monotonic()
    response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    text = response.content if isinstance(response.content, str) else str(response.content)
    logger.info(
        "[agent:root] root_respond LLM done session_id=%s elapsed_s=%.2f reply_len=%s",
        state["session_id"],
        time.monotonic() - t0,
        len(text),
    )

    return {
        "messages": [AIMessage(content=text, name="root_agent")],
        "last_agent_message": text,
        "current_agent": "root",
        "extra": {"message_ui": None},
    }


# ── Save Intake ───────────────────────────────────────────────────────────────

async def save_intake_node(state: AgentState, config: RunnableConfig) -> dict:
    """Persist intake fields from the specialist node into booking_consult_intakes."""
    from app.tools.intake_tools import save_consult_intake

    logger.info(
        "[agent:root] save_intake_node session_id=%s patient_user_id=%s",
        state["session_id"],
        state["patient_user_id"],
    )
    result_json = await save_consult_intake.ainvoke({
        "patient_user_id": state["patient_user_id"],
        "session_id": state["session_id"],
        "symptoms": state.get("symptoms_summary") or "",
        "ai_diagnosis": state.get("ai_diagnosis") or "",
        "needs_visit": state.get("needs_visit", False),
        "dental_case_code": state.get("dental_case_code"),
    })
    data = json.loads(result_json)
    intake_id = data.get("intake_id")
    logger.info(f"[save_intake] intake_id={intake_id}")
    return {"intake_id": intake_id, "triage_complete": True}


# ── Booking prerequisites (direct booking without prior consultation) ───────

async def booking_prepare_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Sau khi triage_complete: nạp/ làm mới available_slots (ví dụ đổi ngày trong tin nhắn).
    Không tạo intake “walk-in” mà không qua chuyên gia.
    """
    from app.tools.schedule_tools import get_mock_schedule, infer_date_str_from_user_text

    updates: dict = {}

    user_text = ""
    for msg in reversed(state["messages"]):
        if hasattr(msg, "type") and msg.type == "human":
            user_text = msg.content if isinstance(msg.content, str) else ""
            break

    if not state.get("triage_complete"):
        logger.warning(
            "[agent:root] booking_prepare called without triage_complete session_id=%s — no-op",
            state["session_id"],
        )
        return updates

    if not state.get("intake_id"):
        logger.error(
            "[agent:root] booking_prepare missing intake_id after triage session_id=%s",
            state["session_id"],
        )
        return updates

    if not state.get("available_slots"):
        date_hint = infer_date_str_from_user_text(user_text)
        slot_kw: dict = {"dental_case_code": state.get("dental_case_code")}
        if date_hint:
            slot_kw["date_str"] = date_hint
        slot_kw["scope"] = "day"
        result_json = await get_mock_schedule.ainvoke(slot_kw)
        data = json.loads(result_json)
        slots = data.get("slots", [])
        updates["available_slots"] = slots
        if data.get("date"):
            updates["pending_booking_date_iso"] = data["date"]
        logger.info(
            "[agent:root] booking_prepare loaded slots=%s date_hint=%s session_id=%s",
            len(slots),
            date_hint,
            state["session_id"],
        )
    elif state.get("available_slots") and not state.get("pending_booking_date_iso"):
        iso = _booking_date_iso_from_state(state)
        if iso:
            updates["pending_booking_date_iso"] = iso

    return updates


# ── Query Available Slots ────────────────────────────────────────────────────

async def query_slots_node(state: AgentState, config: RunnableConfig) -> dict:
    """Fetch slots từ file mock JSON (get_mock_schedule, scope=day)."""
    from app.tools.schedule_tools import get_mock_schedule

    logger.info("[agent:root] query_slots_node session_id=%s", state["session_id"])
    result_json = await get_mock_schedule.ainvoke({
        "scope": "day",
        "dental_case_code": state.get("dental_case_code"),
    })
    data = json.loads(result_json)
    slots = data.get("slots", [])
    logger.info(f"[query_slots] found {len(slots)} slots")
    out: dict = {"available_slots": slots}
    if data.get("date"):
        out["pending_booking_date_iso"] = data["date"]
    return out


# ── Confirm Booking ───────────────────────────────────────────────────────────

async def _finalize_booking_from_pending(state: AgentState, slot: dict) -> dict:
    """Persist reservation after explicit user confirmation."""
    from app.tools.schedule_tools import book_appointment

    intake_id = state.get("intake_id")
    if not intake_id:
        msg = "Đã xảy ra lỗi khi lấy thông tin đặt lịch. Vui lòng thử lại."
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "pending_confirmation_slot": None,
        }

    result_json = await book_appointment.ainvoke({
        "patient_user_id": state["patient_user_id"],
        "intake_id": intake_id,
        "datetime_str": slot["datetime_str"],
    })
    data = json.loads(result_json)

    if "error" in data:
        logger.error(f"[confirm_booking] {data['error']}")
        msg = "Đã xảy ra lỗi khi đặt lịch. Vui lòng thử lại."
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "pending_confirmation_slot": None,
        }

    logger.info(f"[confirm_booking] reservation_id={data['reservation_id']}")
    return {
        "pending_confirmation_slot": None,
        "selected_slot": data["display"],
        "booking_confirmed": True,
        "reservation_id": data["reservation_id"],
        "skip_root_respond": False,
        "extra": {"message_ui": None},
    }


async def confirm_booking_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Check mock availability for the requested time, ask for confirmation, or finalize booking.
    """
    from app.tools.schedule_tools import infer_date_str_from_user_text, resolve_requested_slot

    user_text = _last_human_text(state)
    pending = state.get("pending_confirmation_slot")

    logger.info(
        "[agent:root] confirm_booking_node session_id=%s slots=%s intake_id=%s pending_confirm=%s",
        state["session_id"],
        len(state.get("available_slots") or []),
        state.get("intake_id"),
        bool(pending),
    )

    # ── Explicit confirmation of proposed slot ───────────────────────────────
    if pending and isinstance(pending, dict) and pending.get("datetime_str"):
        if _looks_like_booking_confirmation(user_text):
            return await _finalize_booking_from_pending(state, pending)
        if _looks_like_booking_rejection(user_text):
            msg = (
                "Được ạ. Bạn muốn đổi sang **giờ nào**? Gõ giờ (ví dụ **15:30**) "
                "hoặc nói rõ khung bạn mong muốn."
            )
            return {
                "pending_confirmation_slot": None,
                "messages": [AIMessage(content=msg, name="root_agent")],
                "last_agent_message": msg,
                "current_agent": "root",
                "skip_root_respond": True,
                "extra": {"message_ui": None},
            }

    # ── Parse requested time and check availability (tool-backed mock logic) ─
    hm = _parse_first_time_hm(user_text)
    date_iso = _booking_date_iso_from_state(state)
    if not date_iso:
        date_iso = infer_date_str_from_user_text(user_text)
    if not date_iso:
        date_iso = _default_next_weekday_iso()

    if hm is None:
        slots = state.get("available_slots") or []
        if slots:
            d0 = slots[0].get("display", "")
            date_disp = d0.split("–")[0].strip() if "–" in d0 else "ngày bạn đã chọn"
            if not date_disp:
                date_disp = "ngày bạn đã chọn"
            time_labels = [
                s.get("time_hm")
                or s.get("display", "").split("–")[-1].split("(")[0].strip()
                for s in slots
            ]
            msg = (
                f"Mình đã ghi nhận bạn muốn khám vào **{date_disp}**.\n\n"
                f"**Chọn giờ** trong các nút bên dưới, hoặc **gõ giờ** trong ô chat "
                f"(ví dụ **14:00** hoặc **9h**)."
            )
            return {
                "pending_confirmation_slot": None,
                "messages": [AIMessage(content=msg, name="root_agent")],
                "last_agent_message": msg,
                "current_agent": "root",
                "skip_root_respond": True,
                "extra": {
                    "message_ui": {
                        "template": "time_chips",
                        "date_label": date_disp,
                        "times": time_labels,
                        "dental_case_code": state.get("dental_case_code"),
                    },
                },
            }

        logger.warning(
            "[agent:root] confirm_booking no time and no slots session_id=%s",
            state["session_id"],
        )
        msg = (
            "Bạn vui lòng cho mình biết **ngày** và **giờ** khám mong muốn "
            "(ví dụ: thứ 6 tuần này, lúc 14:00)."
        )
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {"message_ui": None},
        }

    hour, minute = hm
    res = resolve_requested_slot(date_iso, hour, minute, state.get("dental_case_code"))

    if res["kind"] == "closed":
        msg = (
            "Ngày này phòng khám không làm việc hoặc không có trong lịch. "
            "Bạn chọn **thứ 2–thứ 6** và một giờ trong ca (08:00–17:00) nhé."
        )
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {"message_ui": None},
        }

    if res["kind"] == "exact_available":
        slot = res["slot"]
        msg = (
            f"Khung **{slot['display']}** hiện **còn trống**. "
            f"Mình có thể **giữ chỗ** này cho bạn. "
            f"Bạn **xác nhận đặt lịch** giúp mình nhé?"
        )
        return {
            "pending_confirmation_slot": slot,
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {
                "message_ui": {
                    "template": "confirm_actions",
                    "slot_display": slot["display"],
                    "date_iso": _date_iso_from_slot_datetime(slot.get("datetime_str")),
                    "dental_case_code": slot.get("dental_case_code")
                    or state.get("dental_case_code"),
                },
            },
        }

    # suggest nearest free slots
    alts = res.get("alternatives") or []
    lbl = res.get("requested_label", f"{hour:02d}:{minute:02d}")
    if alts:
        alt_labels = [
            a.get("time_hm")
            or a.get("display", "").split("–")[-1].split("(")[0].strip()
            for a in alts[:6]
        ]
        msg = (
            f"Khung **{lbl}** bạn chọn hiện **đã kín** hoặc không còn trong lịch trống.\n\n"
            f"**Chọn một giờ gần nhất** bên dưới hoặc gõ lại giờ trong ô chat."
        )
        return {
            "pending_confirmation_slot": None,
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {
                "message_ui": {
                    "template": "time_chips",
                    "date_label": f"Gợi ý (thay cho {lbl})",
                    "times": alt_labels,
                    "dental_case_code": state.get("dental_case_code"),
                },
            },
        }

    msg = (
        f"Không tìm thấy khung trống gần **{lbl}**. "
        f"Bạn thử giờ khác trong ca làm việc (08:00–17:00, thứ 2–thứ 6) nhé."
    )
    return {
        "messages": [AIMessage(content=msg, name="root_agent")],
        "last_agent_message": msg,
        "current_agent": "root",
        "skip_root_respond": True,
        "extra": {"message_ui": None},
    }
