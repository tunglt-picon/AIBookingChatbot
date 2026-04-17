"""
Root orchestrator (text LLM / configurable).

Responsibilities:
  1. classify_intent_node – route user intent
  2. root_respond_node    – compose replies (FAQ, slot list, booking confirmation)
  3. save_intake_node     – persist BookingConsultIntake after the specialist path
  4. booking_prepare_node – ensure intake + slots exist for direct booking flows
  5. confirm_booking_node – match a slot and create a Reservation
"""

import json
import logging
import re
import time
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.llm_log_utils import format_llm_response_for_log, message_content_as_text
from app.agents.llm_factory import get_root_llm
from app.config import settings
from app.observability.langfuse_client import get_langfuse_callback

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _format_prompt_for_log(messages: list) -> str:
    blocks: list[str] = []
    for i, msg in enumerate(messages, start=1):
        role = getattr(msg, "type", type(msg).__name__)
        name = getattr(msg, "name", "") or "-"
        content = msg.content if isinstance(getattr(msg, "content", ""), str) else str(getattr(msg, "content", ""))
        blocks.append(
            f"[{i}] role={role} name={name}\n"
            f"{content}"
        )
    return "\n\n------------------------------\n\n".join(blocks)

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
        "đồng ý", "dong y", "đúng rồi", "dung roi", "xác nhận", "xac nhan",
        "ok", "chốt", "chot", "đặt luôn", "dat luon", "yes", "confirm",
        "nhất trí", "nhat tri", "đặt giúp", "dat giup", "giữ giúp", "giu giup",
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


def _looks_like_post_triage_booking_yes(text: str) -> bool:
    """Xác nhận ngắn muốn đặt lịch sau khi đã triage (không kèm giờ cụ thể)."""
    low = text.lower().strip()
    if len(low) > 48:
        return False
    if _parse_first_time_hm(text) is not None:
        return False
    keys = (
        "đúng rồi", "dung roi", "vâng ạ", "vang a", "vâng", "vang",
        "dạ ạ", "da a", "chính xác", "chinh xac", "ừm", "um ", "uhm",
        "ok", "yes", "có ạ", "co a",
    )
    if any(k in low for k in keys):
        return True
    short = low.rstrip(".,!?…")
    return short in ("ừ", "uh", "dạ", "ạ", "vâng", "có")


def _looks_like_category_confirmation_yes(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low:
        return False
    # Match đúng label button "Đúng nhóm này" (ưu tiên) + vài biến thể ngắn.
    keys = (
        "đúng nhóm này", "dung nhom nay", "đúng nhóm", "dung nhom",
        "nhóm này", "nhom nay", "xác nhận", "xac nhan",
        "đúng rồi", "dung roi", "đồng ý", "dong y",
    )
    return any(k in low for k in keys)


def _looks_like_category_confirmation_no(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low:
        return False
    # Match đúng label button "Nhóm khác" + vài biến thể rõ ràng (tránh dính với "không" chung).
    keys = (
        "nhóm khác", "nhom khac", "đổi nhóm", "doi nhom",
        "chưa đúng", "chua dung", "sai nhóm", "sai nhom",
        "không đúng nhóm", "khong dung nhom",
    )
    return any(k in low for k in keys)


def _looks_like_booking_intent(text: str) -> bool:
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


def _slot_display_date_part(display: str) -> str:
    if not display or not isinstance(display, str):
        return ""
    m = re.match(r"^(.+?)\s*[–\-]\s*.+$", display.strip())
    return m.group(1).strip() if m else display.strip()


def _time_labels_from_slots(slots: list) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in slots:
        if not isinstance(s, dict):
            continue
        label = ""
        hm = s.get("time_hm")
        if isinstance(hm, str) and hm.strip():
            m = re.search(r"\b(\d{1,2}:\d{2})\b", hm.strip())
            label = m.group(1) if m else hm.strip()
        if not label:
            disp = s.get("display") or ""
            m2 = re.search(r"\b(\d{1,2}:\d{2})\b", disp)
            label = m2.group(1) if m2 else ""
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _parse_first_time_hm(user_text: str) -> tuple[int, int] | None:
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


def _date_iso_from_slot_datetime(datetime_str: str | None) -> str:
    from datetime import datetime
    if not datetime_str or not isinstance(datetime_str, str):
        return ""
    try:
        normalized = datetime_str.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        return datetime_str[:10] if len(datetime_str) >= 10 else ""


def _build_day_chips_payload() -> dict[str, Any]:
    labels = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Ngày mai"]
    return {"template": "day_chips", "days": labels}


def _build_category_confirm_payload(category_code: str | None) -> dict[str, Any]:
    return {
        "template": "category_confirm",
        "category_code": category_code or "",
        "actions": ["Đúng nhóm này", "Nhóm khác"],
    }


def _looks_like_category_count_question(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low:
        return False
    ask_keys = ("bao nhiêu", "mấy", "may", "số lượng", "so luong")
    domain_keys = (
        "nhóm", "nhom",
        "category", "cat",
        "loại khám", "loai kham",
        "dịch vụ", "dich vu",
        "danh mục", "danh muc",
        "hạng mục", "hang muc",
    )
    clinic_keys = ("phòng khám", "phong kham", "smilecare")
    return (
        any(k in low for k in ask_keys)
        and any(k in low for k in domain_keys)
        and any(k in low for k in clinic_keys)
    )


def _build_mock_categories_reply() -> str:
    from app.domain.dental_cases import category_label_vi, category_short_description_vi
    from app.services.mock_week_schedule_loader import mock_schedule_summary_for_lab

    summary = mock_schedule_summary_for_lab()
    codes = summary.get("cac_ma_loai_kham_trong_file") or []
    norm_codes = [str(c).strip().upper() for c in codes if isinstance(c, str) and str(c).strip()]
    if not norm_codes:
        norm_codes = ["CAT-01", "CAT-02", "CAT-03", "CAT-04", "CAT-05"]

    lines = [f"Theo dữ liệu mock hiện tại, phòng khám có **{len(norm_codes)} nhóm khám**:"]
    for code in norm_codes:
        label = category_label_vi(code)
        short = category_short_description_vi(code)
        if short:
            lines.append(f"- **{code} — {label}**: {short}")
        else:
            lines.append(f"- **{code} — {label}**")
    lines.append("\nBạn có thể mô tả triệu chứng, mình sẽ giúp phân vào nhóm phù hợp và gợi ý lịch trống.")
    return "\n".join(lines)


# ── Intent Classification ─────────────────────────────────────────────────────

_INTENT_SYSTEM = """\
Bạn là AI phân loại ý định của phòng khám nha khoa. Phân tích tin nhắn cuối cùng của bệnh nhân \
và phân loại thành MỘT trong các nhãn sau:

- consultation : bệnh nhân mô tả triệu chứng, vấn đề răng miệng, \
  hoặc đang tiếp tục cuộc trò chuyện tư vấn đang diễn ra
- select_slot  : bệnh nhân muốn đặt lịch, nêu ngày và/hoặc giờ khám, hoặc chọn khung giờ
- confirm_appointment : bệnh nhân đồng ý / OK / xác nhận khi bot vừa hỏi có đặt lịch không
- general      : câu hỏi chung, lời chào, câu hỏi về phòng khám, chủ đề không liên quan

Ngữ cảnh hiện tại:
  - Cuộc tư vấn đang diễn ra: {has_consultation}
  - Đã hoàn tất triage (được phép chọn giờ): {triage_complete}
  - Đã hiển thị khung giờ: {has_slots}
  - Số câu hỏi làm rõ đã hỏi: {follow_up_count}/{max_follow_ups}
  - Đang chờ xác nhận lịch: {pending_confirm_context}

Quy tắc routing:
  - Nếu triage_complete = chưa, và BN muốn đặt lịch hoặc nói ngày/giờ → **consultation** (cần mô tả triệu chứng trước).

Chỉ trả lời bằng MỘT nhãn: consultation | select_slot | confirm_appointment | general
"""


async def classify_intent_node(state: AgentState, config: RunnableConfig) -> dict:
    llm = get_root_llm()
    callbacks = get_langfuse_callback(config)

    last_human = _last_human_text(state)

    # Category confirmation gate (sau specialist chốt)
    if state.get("pending_category_confirmation"):
        if _looks_like_category_confirmation_no(last_human):
            logger.info("[classify_intent] category rejected -> consultation session_id=%s", state["session_id"])
            return {
                "intent": "consultation",
                "current_agent": "root",
                "skip_root_respond": False,
                "pending_category_confirmation": False,
                "triage_complete": False,
                "specialist_concluded": False,
                # Reset để specialist được quyền hỏi lại chi tiết thay vì force_conclusion ngay.
                "follow_up_count": 0,
                "category_code": None,
                "available_slots": [],
                "pending_booking_date_iso": None,
                "pending_confirmation_slot": None,
                "selected_slot": None,
                "booking_confirmed": False,
                "reservation_id": None,
            }
        if _looks_like_category_confirmation_yes(last_human) or _looks_like_booking_intent(last_human):
            logger.info("[classify_intent] category confirmed -> select_slot session_id=%s", state["session_id"])
            return {
                "intent": "select_slot",
                "current_agent": "root",
                "skip_root_respond": False,
                "pending_category_confirmation": False,
                "available_slots": [],
                "pending_booking_date_iso": None,
                "pending_confirmation_slot": None,
                "selected_slot": None,
                "booking_confirmed": False,
                "reservation_id": None,
            }

    # Shortcut: explicit confirmation when pending
    if state.get("pending_confirmation_slot") and _looks_like_booking_confirmation(last_human):
        logger.info("[classify_intent] shortcut confirm_appointment session_id=%s", state["session_id"])
        return {"intent": "confirm_appointment", "current_agent": "root", "skip_root_respond": False}

    if state.get("pending_confirmation_slot") and _looks_like_booking_rejection(last_human):
        logger.info("[classify_intent] clear pending_confirmation (rejection) session_id=%s", state["session_id"])
        return {
            "intent": "select_slot",
            "current_agent": "root",
            "pending_confirmation_slot": None,
            "skip_root_respond": False,
        }

    # Post-triage affirmation → go to slot selection
    if (
        state.get("triage_complete")
        and not state.get("pending_confirmation_slot")
        and _looks_like_post_triage_booking_yes(last_human)
    ):
        logger.info("[classify_intent] shortcut select_slot (post-triage yes) session_id=%s", state["session_id"])
        # Reset booking-related transient state để luôn hỏi "bạn rảnh thứ mấy"
        # (tránh dùng lại state cũ do Redis checkpoint).
        return {
            "intent": "select_slot",
            "current_agent": "root",
            "skip_root_respond": False,
            "available_slots": [],
            "pending_booking_date_iso": None,
            "pending_confirmation_slot": None,
            "booking_confirmed": False,
            "reservation_id": None,
            "selected_slot": None,
        }

    # Booking-like text without triage → force consultation first
    if not state.get("triage_complete") and _looks_like_booking_intent(last_human):
        logger.info("[classify_intent] booking without triage -> consultation session_id=%s", state["session_id"])
        return {"intent": "consultation", "current_agent": "root", "skip_root_respond": False}

    has_consultation = bool(state.get("symptoms_summary") or state.get("follow_up_count", 0) > 0)
    has_slots = bool(state.get("available_slots"))
    pending = state.get("pending_confirmation_slot")
    pending_confirm_context = f"có — {pending.get('display')}" if pending and isinstance(pending, dict) and pending.get("display") else "không"
    triage_done = bool(state.get("triage_complete"))

    system_content = _INTENT_SYSTEM.format(
        has_consultation=has_consultation,
        triage_complete="có" if triage_done else "chưa",
        has_slots=has_slots,
        follow_up_count=state.get("follow_up_count", 0),
        max_follow_ups=settings.MAX_FOLLOW_UP_QUESTIONS,
        pending_confirm_context=pending_confirm_context,
    )

    recent_messages = state["messages"][-6:]
    prompt = [SystemMessage(content=system_content)] + recent_messages

    logger.info(
        "[agent:root] classify_intent LLM invoke start session_id=%s msgs_in_prompt=%s",
        state["session_id"], len(prompt),
    )
    logger.info(
        "[agent:root] classify_intent PROMPT session_id=%s\n\n%s\n",
        state["session_id"],
        _format_prompt_for_log(prompt),
    )
    t0 = time.monotonic()
    response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    logger.info(
        "[agent:root] classify_intent LLM RESPONSE session_id=%s\n\n%s\n",
        state["session_id"],
        format_llm_response_for_log(response),
    )
    logger.info(
        "[agent:root] classify_intent LLM done session_id=%s elapsed_s=%.2f",
        state["session_id"], time.monotonic() - t0,
    )
    intent_raw = message_content_as_text(response.content).strip().lower()

    if "consultation" in intent_raw:
        intent = "consultation"
    elif "confirm_appointment" in intent_raw:
        intent = "confirm_appointment"
    elif "select_slot" in intent_raw:
        intent = "select_slot"
    else:
        intent = "general"

    logger.info("[classify_intent] session=%s intent=%r", state["session_id"], intent)
    return {"intent": intent, "current_agent": "root", "skip_root_respond": False}


# ── General Response ──────────────────────────────────────────────────────────

_ROOT_SYSTEM = """\
Bạn là trợ lý AI thân thiện của Phòng Khám Nha Khoa SmileCare. \
Nhiệm vụ: chào đón bệnh nhân, thu thập thông tin ban đầu, hướng dẫn đặt lịch.

Thông tin phòng khám:
  - Địa chỉ: 101 Lê Lợi, Thành phố Đà Nẵng
  - Giờ làm việc: Thứ 2–6, 08:00–17:00
  - Hotline: 028-1234-5678

Quy định:
  - KHÔNG tư vấn y khoa, KHÔNG chẩn đoán, KHÔNG kê đơn.
  - Chỉ hỗ trợ đặt lịch và thu thập triệu chứng ban đầu.
  - **TUYỆT ĐỐI KHÔNG hỏi họ tên, số điện thoại, email, địa chỉ, CMND/CCCD, ngày sinh**
    hay bất kỳ thông tin hành chính/định danh nào. Bệnh nhân đã đăng nhập — hệ thống đã có các thông tin này.
  - Trả lời ngắn gọn, thân thiện, bằng tiếng Việt.
"""


async def root_respond_node(state: AgentState, config: RunnableConfig) -> dict:
    llm = get_root_llm()
    callbacks = get_langfuse_callback(config)
    last_human = _last_human_text(state)

    if _looks_like_category_count_question(last_human):
        msg = _build_mock_categories_reply()
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {"message_ui": None},
        }

    # Sau triage: bắt buộc xác nhận category trước khi qua bước chọn ngày.
    if state.get("triage_complete") and state.get("pending_category_confirmation"):
        from app.domain.dental_cases import category_label_vi, category_short_description_vi

        cat = state.get("category_code")
        cat_label = category_label_vi(cat) if cat else "nhóm khám phù hợp"
        cat_desc = category_short_description_vi(cat) if cat else ""
        if cat_desc:
            msg = (
                f"Mình đang phân loại bạn vào nhóm **{cat_label}** "
                f"— {cat_desc}\n\n"
                "Bạn xác nhận giúp mình phân loại này đã đúng chưa?"
            )
        else:
            msg = (
                f"Mình đang phân loại bạn vào nhóm **{cat_label}**. "
                "Bạn xác nhận giúp mình phân loại này đã đúng chưa?"
            )
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {"message_ui": _build_category_confirm_payload(cat)},
        }

    # Sau khi đã triage xong, luôn hỏi ngày rảnh trước (không tự chốt ngày mặc định).
    if state.get("triage_complete") and not state.get("booking_confirmed") and not state.get("available_slots"):
        msg = (
            "Mình đã ghi nhận nhóm khám phù hợp. "
            "Bạn vui lòng cho mình biết **bạn rảnh vào thứ mấy** "
            "(có thể chọn **1 hoặc nhiều ngày**, ví dụ: *Thứ 3, Thứ 5*)."
        )
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {"message_ui": _build_day_chips_payload()},
        }

    extra_context = ""
    if state.get("category_code"):
        from app.domain.dental_cases import category_label_vi
        extra_context += f"\nĐã phân loại nhu cầu khám: **{category_label_vi(state['category_code'])}**.\n"

    if state.get("available_slots"):
        slots_text = "\n".join(f"  • {s['display']}" for s in state["available_slots"])
        extra_context += f"\nCác khung giờ trống:\n{slots_text}\nHãy hỏi bệnh nhân muốn chọn khung giờ nào."

    if state.get("booking_confirmed") and state.get("reservation_id"):
        slot_display = state.get("selected_slot", "")
        extra_context += f"\nĐặt lịch thành công! Mã đặt lịch: #{state['reservation_id']}. Giờ hẹn: {slot_display}."

    system = _ROOT_SYSTEM + extra_context
    recent_messages = state["messages"][-10:]
    prompt = [SystemMessage(content=system)] + recent_messages

    logger.info(
        "[agent:root] root_respond LLM invoke session_id=%s has_slots=%s booking_confirmed=%s",
        state["session_id"], bool(state.get("available_slots")), bool(state.get("booking_confirmed")),
    )
    logger.info(
        "[agent:root] root_respond PROMPT session_id=%s\n\n%s\n",
        state["session_id"],
        _format_prompt_for_log(prompt),
    )
    t0 = time.monotonic()
    response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    logger.info(
        "[agent:root] root_respond LLM RESPONSE session_id=%s\n\n%s\n",
        state["session_id"],
        format_llm_response_for_log(response),
    )
    text = message_content_as_text(response.content)
    logger.info(
        "[agent:root] root_respond done session_id=%s elapsed_s=%.2f len=%s",
        state["session_id"], time.monotonic() - t0, len(text),
    )

    return {
        "messages": [AIMessage(content=text, name="root_agent")],
        "last_agent_message": text,
        "current_agent": "root",
        "extra": {"message_ui": None},
    }


# ── Save Intake ───────────────────────────────────────────────────────────────

async def save_intake_node(state: AgentState, config: RunnableConfig) -> dict:
    from app.tools.intake_tools import save_consult_intake

    logger.info(
        "[agent:root] save_intake_node session_id=%s patient_user_id=%s",
        state["session_id"], state["patient_user_id"],
    )
    result_json = await save_consult_intake.ainvoke({
        "patient_user_id": state["patient_user_id"],
        "session_id": state["session_id"],
        "symptoms": state.get("symptoms_summary") or "",
        "ai_diagnosis": state.get("ai_diagnosis") or "",
        "needs_visit": True,
        "category_code": state.get("category_code"),
    })
    data = json.loads(result_json)
    intake_id = data.get("intake_id")
    logger.info("[save_intake] intake_id=%s", intake_id)
    return {
        "intake_id": intake_id,
        "triage_complete": True,
        "pending_category_confirmation": True,
        # Reset booking-related state right after triage is persisted.
        "available_slots": [],
        "pending_booking_date_iso": None,
        "pending_confirmation_slot": None,
        "selected_slot": None,
        "booking_confirmed": False,
        "reservation_id": None,
    }


# ── Booking prerequisites ─────────────────────────────────────────────────────

async def booking_prepare_node(state: AgentState, config: RunnableConfig) -> dict:
    from app.tools.schedule_tools import get_mock_schedule, infer_date_str_from_user_text

    updates: dict = {}

    user_text = ""
    for msg in reversed(state["messages"]):
        if hasattr(msg, "type") and msg.type == "human":
            user_text = msg.content if isinstance(msg.content, str) else ""
            break

    if not state.get("triage_complete"):
        logger.warning("[agent:root] booking_prepare without triage session_id=%s — no-op", state["session_id"])
        return updates

    if not state.get("intake_id"):
        logger.error("[agent:root] booking_prepare missing intake_id session_id=%s", state["session_id"])
        return updates

    if not state.get("available_slots"):
        date_hint = infer_date_str_from_user_text(user_text)
        if not date_hint:
            logger.info(
                "[agent:root] booking_prepare waiting for day preference session_id=%s",
                state["session_id"],
            )
            return updates
        slot_kw: dict = {"category_code": state.get("category_code")}
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
            "[agent:root] booking_prepare loaded slots=%s date=%s session_id=%s",
            len(slots), date_hint, state["session_id"],
        )
    elif state.get("available_slots") and not state.get("pending_booking_date_iso"):
        iso = _booking_date_iso_from_state(state)
        if iso:
            updates["pending_booking_date_iso"] = iso

    return updates


# ── Query Available Slots ────────────────────────────────────────────────────

async def query_slots_node(state: AgentState, config: RunnableConfig) -> dict:
    logger.info("[agent:root] query_slots_node session_id=%s -> defer until patient picks day", state["session_id"])
    # Không tự query ngày mặc định ngay sau triage để tránh ép về Thứ 2.
    return {"available_slots": [], "pending_booking_date_iso": None}


# ── Confirm Booking ───────────────────────────────────────────────────────────

async def _finalize_booking_from_pending(state: AgentState, slot: dict) -> dict:
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
        logger.error("[confirm_booking] %s", data["error"])
        msg = "Đã xảy ra lỗi khi đặt lịch. Vui lòng thử lại."
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "pending_confirmation_slot": None,
        }

    logger.info("[confirm_booking] reservation_id=%s", data["reservation_id"])
    return {
        "pending_confirmation_slot": None,
        "selected_slot": data["display"],
        "booking_confirmed": True,
        "reservation_id": data["reservation_id"],
        "skip_root_respond": False,
        "extra": {"message_ui": None},
    }


async def confirm_booking_node(state: AgentState, config: RunnableConfig) -> dict:
    from app.tools.schedule_tools import (
        get_mock_schedule,
        infer_date_str_from_user_text,
        infer_date_strs_from_user_text,
        resolve_requested_slot,
    )

    user_text = _last_human_text(state)
    pending = state.get("pending_confirmation_slot")

    logger.info(
        "[agent:root] confirm_booking_node session_id=%s slots=%s intake_id=%s pending=%s",
        state["session_id"], len(state.get("available_slots") or []),
        state.get("intake_id"), bool(pending),
    )

    # Explicit confirmation of proposed slot
    if pending and isinstance(pending, dict) and pending.get("datetime_str"):
        if _looks_like_booking_confirmation(user_text):
            return await _finalize_booking_from_pending(state, pending)
        if _looks_like_booking_rejection(user_text):
            msg = "Được ạ. Bạn muốn đổi sang **giờ nào**? Gõ giờ (ví dụ **15:30**) hoặc nói rõ khung mong muốn."
            return {
                "pending_confirmation_slot": None,
                "messages": [AIMessage(content=msg, name="root_agent")],
                "last_agent_message": msg,
                "current_agent": "root",
                "skip_root_respond": True,
                "extra": {"message_ui": None},
            }

    # Parse requested time
    hm = _parse_first_time_hm(user_text)
    date_hints = infer_date_strs_from_user_text(user_text)
    date_iso = _booking_date_iso_from_state(state)
    if not date_iso:
        date_iso = infer_date_str_from_user_text(user_text)

    if hm is None:
        # User hiện tại chưa cung cấp giờ => bắt buộc phải cung cấp "thứ mấy" trong message này.
        # Không fallback theo state cũ để tránh việc Redis checkpoint làm bot nhảy nhầm ngày (vd: tự đưa Thứ 2).
        if not date_hints:
            msg = (
                "Trước khi chọn giờ, bạn vui lòng cho mình biết "
                "**bạn rảnh vào thứ mấy** "
                "(có thể chọn **1 hoặc nhiều ngày**, ví dụ: *Thứ 3, Thứ 5*)."
            )
            return {
                "messages": [AIMessage(content=msg, name="root_agent")],
                "last_agent_message": msg,
                "current_agent": "root",
                "skip_root_respond": True,
                "extra": {"message_ui": _build_day_chips_payload()},
            }

        candidate_dates = date_hints
        all_slots: list[dict] = []
        days_without_slots: list[str] = []
        for d_iso in candidate_dates:
            day_json = await get_mock_schedule.ainvoke({
                "scope": "day",
                "date_str": d_iso,
                "category_code": state.get("category_code"),
            })
            day_data = json.loads(day_json)
            day_slots = day_data.get("slots", [])
            if not day_slots:
                days_without_slots.append(d_iso)
            all_slots.extend(day_slots[:6])

        if all_slots:
            all_slots = all_slots[:18]
            day_labels = sorted({_slot_display_date_part(s.get("display", "")) for s in all_slots if s.get("display")})
            day_desc = ", ".join([d for d in day_labels if d]) or "các ngày bạn đã chọn"
            miss_desc = ", ".join(days_without_slots) if days_without_slots else ""
            msg = (
                f"Mình đã kiểm tra lịch cho **{day_desc}**. "
                "Bạn chọn **khung giờ** phù hợp trong các gợi ý bên dưới nhé."
            )
            if miss_desc:
                msg += f"\n\n(Lưu ý: hiện chưa có slot cho: {miss_desc})"
            chip_labels = [s.get("display", "").replace(" – ", " ") for s in all_slots if s.get("display")]
            chip_labels = [c.strip() for c in chip_labels if c.strip()]
            return {
                "available_slots": all_slots,
                "pending_booking_date_iso": None,
                "pending_confirmation_slot": None,
                "messages": [AIMessage(content=msg, name="root_agent")],
                "last_agent_message": msg,
                "current_agent": "root",
                "skip_root_respond": True,
                "extra": {
                    "message_ui": {
                        "template": "datetime_chips",
                        "options": chip_labels[:12],
                        "category_code": state.get("category_code"),
                    },
                },
            }

        slots = state.get("available_slots") or []
        if slots:
            d0 = slots[0].get("display", "")
            date_disp = _slot_display_date_part(d0) or "ngày đề xuất"
            time_labels = _time_labels_from_slots(slots)
            msg = (
                f"Dưới đây là **các khung giờ còn trống** cho **{date_disp}**.\n\n"
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
                        "category_code": state.get("category_code"),
                    },
                },
            }

        logger.warning("[agent:root] confirm_booking no time and no slots session_id=%s", state["session_id"])
        msg = "Mình chưa tìm được lịch phù hợp. Bạn vui lòng chọn lại **thứ bạn rảnh** nhé."
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {"message_ui": _build_day_chips_payload()},
        }

    if hm is not None and not date_iso:
        msg = "Bạn đã chọn giờ rồi. Mình cần thêm **thứ bạn rảnh** để kiểm tra chính xác lịch trống."
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {"message_ui": _build_day_chips_payload()},
        }

    hour, minute = hm
    res = resolve_requested_slot(date_iso, hour, minute, state.get("category_code"))

    if res["kind"] == "closed":
        msg = "Ngày này phòng khám không làm việc. Bạn chọn **thứ 2–thứ 6** và giờ trong ca (08:00–17:00) nhé."
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
                    "category_code": slot.get("category_code") or state.get("category_code"),
                },
            },
        }

    # Suggest nearest free slots
    alts = res.get("alternatives") or []
    lbl = res.get("requested_label", f"{hour:02d}:{minute:02d}")
    if alts:
        alt_text = "\n".join(f"  • {a['display']}" for a in alts[:6])
        msg = (
            f"Khung **{lbl}** không còn trống. Các khung gần nhất:\n{alt_text}\n\n"
            f"Bạn muốn chọn khung nào?"
        )
        time_labels = _time_labels_from_slots(alts[:6])
        return {
            "messages": [AIMessage(content=msg, name="root_agent")],
            "last_agent_message": msg,
            "current_agent": "root",
            "skip_root_respond": True,
            "extra": {
                "message_ui": {
                    "template": "time_chips",
                    "date_label": "",
                    "times": time_labels,
                    "category_code": state.get("category_code"),
                },
            },
        }

    msg = f"Khung **{lbl}** không còn trống và không tìm thấy khung thay thế. Bạn thử giờ khác nhé."
    return {
        "messages": [AIMessage(content=msg, name="root_agent")],
        "last_agent_message": msg,
        "current_agent": "root",
        "skip_root_respond": True,
        "extra": {"message_ui": None},
    }
