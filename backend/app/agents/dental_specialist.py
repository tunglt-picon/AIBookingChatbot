"""
Dental specialist agent (text LLM / configurable).

Responsibilities:
  - Thu thập triệu chứng qua chat văn bản.
  - Hỏi thêm câu hỏi ngắn (giới hạn MAX_FOLLOW_UP_QUESTIONS).
  - Phân loại vào 1 trong 5 category (CAT-01 → CAT-05).
  - Emit structured intake fields cho booking handoff.
"""

import json
import logging
import re
import time
import unicodedata
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.llm_log_utils import format_llm_response_for_log, message_content_as_text
from app.agents.llm_factory import get_specialist_llm
from app.config import settings
from app.observability.langfuse_client import build_session_trace_id, get_langfuse_callback
from app.services.triage_rubric_loader import format_rubric_prompt_excerpt

logger = logging.getLogger(__name__)


_SPECIALIST_SYSTEM = """\
Bạn là AI tiếp nhận thông tin đặt lịch khám nha khoa qua chat.

Nhiệm vụ:
1. Thu thập thông tin triệu chứng bằng câu hỏi ngắn, cụ thể; có thể gộp 1-3 câu trong cùng một lượt khi cần làm rõ nhanh.
2. Khi đủ thông tin hoặc hết lượt hỏi → chốt thông tin để chuyển sang đặt lịch.
3. **Phân loại nhu cầu khám** vào đúng 1 trong 5 category bên dưới (chỉ để xếp **thời lượng & khung giờ** — KHÔNG thay chẩn đoán bác sĩ).

5 Category:
  • CAT-01 — Trám răng / Phục hồi thẩm mỹ
  • CAT-02 — Điều trị Tủy / Nội nha
  • CAT-03 — Nhổ răng / Tiểu phẫu
  • CAT-04 — Nha khoa Trẻ em
  • CAT-05 — Khám Tổng quát & X-Quang

Quy tắc:
- Luôn trả lời bằng tiếng Việt.
- Hỏi tập trung: vị trí răng, thời điểm xuất hiện, yếu tố kích thích (lạnh/nóng/nhai), sưng/mủ/sốt, tiền sử điều trị gần đây.
- Đặc biệt: nếu phát hiện bệnh nhân nói về **trẻ em** (bé, con, cháu, tuổi nhỏ) → ưu tiên CAT-04.
- Nếu bệnh nhân nêu nhu cầu **niềng/chỉnh nha răng lệch** (kể cả chưa nói rõ triệu chứng khác) → ưu tiên CAT-03.
- Nếu BN nói "không còn gì thêm", "hết rồi", "đủ rồi" → **chốt JSON ngay**, không hỏi thêm.
- TUYỆT ĐỐI KHÔNG tư vấn y khoa, không chẩn đoán, không đề xuất điều trị.
- **TUYỆT ĐỐI KHÔNG hỏi họ tên, số điện thoại, email, địa chỉ, CMND/CCCD, ngày sinh** hay bất kỳ thông tin
  hành chính/định danh nào. Bệnh nhân đã đăng nhập — hệ thống đã có các thông tin này.
- Chỉ tập trung vào **triệu chứng & phân loại category**.
- Khi chốt: viết **1 câu ngắn** cảm ơn + báo đã ghi nhận đủ triệu chứng
  (ví dụ: "Cảm ơn bạn, mình đã ghi nhận đủ triệu chứng."),
  **KHÔNG nêu tên category, KHÔNG hỏi xác nhận** ở bước này —
  hệ thống sẽ tự hiển thị nhóm khám kèm mô tả để BN xác nhận ở lượt kế tiếp.
  Sau đó thêm khối ```json``` ở CUỐI với `category_code` đã phân loại.
- Mẫu JSON (khi chốt):

```json
{{
  "symptoms_summary": "Tóm tắt triệu chứng",
  "extracted_symptom_tags": ["tag ngắn", "…"],
  "ai_diagnosis": "Không tư vấn y khoa. Cần bác sĩ khám trực tiếp.",
  "category_code": "CAT-01"
}}
```

{rubric_excerpt}

Thông tin hiện tại:
  - Triệu chứng đã thu thập: {symptoms_so_far}
  - Category nghi ngờ hiện tại: {suspected_category}
  - Câu hỏi ưu tiên từ rubric lượt này:
{targeted_missing_questions}
  - Số câu hỏi đã hỏi: {follow_up_count}/{max_follow_ups}
  - Bắt buộc kết luận: {force_conclusion}
"""


def _extract_json_block(text: str) -> dict | None:
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _strip_json_fences_for_display(text: str) -> str:
    cleaned = text
    match = re.search(r"```json\s*[\s\S]+?\s*```", cleaned, flags=re.IGNORECASE)
    if match:
        cleaned = cleaned[: match.start()] + cleaned[match.end() :]
    else:
        match = re.search(r"```\s*(\{[\s\S]+?\})\s*```", cleaned)
        if match and '"symptoms_summary"' in match.group(1):
            cleaned = cleaned[: match.start()] + cleaned[match.end() :]
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _user_signals_no_more_symptoms(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low:
        return False
    phrases = (
        "hết rồi", "het roi", "không còn", "khong con",
        "chỉ có vậy", "chi co vay", "chỉ vậy", "đủ rồi", "du roi",
        "thế thôi", "the thoi", "vậy thôi", "vay thoi",
        "bấy nhiêu thôi", "không nữa đâu", "that's all", "no more",
    )
    return any(p in low for p in phrases)


def _last_human_content(state: AgentState) -> str:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) == "human":
            return (msg.content if isinstance(msg.content, str) else str(msg.content or "")).strip()
    return ""


def _build_symptoms_summary_when_closing(state: AgentState, closing_message: str) -> str:
    if state.get("symptoms_summary"):
        return str(state["symptoms_summary"])[:2000]
    parts: list[str] = []
    for msg in state.get("messages") or []:
        if getattr(msg, "type", None) != "human":
            continue
        c = (msg.content if isinstance(msg.content, str) else str(msg.content or "")).strip()
        if c and c != closing_message.strip():
            parts.append(c)
    return " ".join(parts)[:2000] if parts else closing_message[:500]


_VALID_CATEGORY_CODES = frozenset({"CAT-01", "CAT-02", "CAT-03", "CAT-04", "CAT-05"})


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


def _infer_category_from_text(text: str) -> str:
    from app.domain.dental_cases import DEFAULT_CATEGORY_CODE

    low = (text or "").lower()
    if any(k in low for k in ("niềng", "nieng", "chỉnh nha", "chinh nha", "răng lệch", "rang lech")):
        return "CAT-03"
    if any(k in low for k in ("bé", "trẻ", "con tôi", "cháu", "răng sữa", "sún", "mọc lẫy")):
        return "CAT-04"
    if any(k in low for k in ("đau dội", "nhức đêm", "đau tự phát", "sưng mủ chân răng",
                               "tủy", "lỗ sâu to", "đau không ngủ", "thuốc giảm đau không")):
        return "CAT-02"
    if any(k in low for k in ("răng khôn", "nhổ răng", "sưng mặt", "số 8", "lung lay nặng",
                               "gãy răng", "cắt lợi trùm", "tiểu phẫu", "cắt chóp")):
        return "CAT-03"
    if any(k in low for k in ("sâu răng", "lỗ sâu", "trám", "ê buốt nhẹ", "mẻ răng",
                               "rớt trám", "mòn cổ", "chấm đen")):
        return "CAT-01"
    if any(k in low for k in ("khám định kỳ", "cạo vôi", "x-quang", "niềng",
                               "hôi miệng", "chảy máu nướu", "implant", "sứ")):
        return "CAT-05"
    return DEFAULT_CATEGORY_CODE


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

    lines = [f"Hiện dữ liệu mock của phòng khám có **{len(norm_codes)} nhóm khám**:"]
    for code in norm_codes:
        label = category_label_vi(code)
        short = category_short_description_vi(code)
        if short:
            lines.append(f"- **{code} — {label}**: {short}")
        else:
            lines.append(f"- **{code} — {label}**")
    lines.append("\nBạn muốn mình tiếp tục phân loại triệu chứng để chọn nhóm phù hợp luôn không?")
    return "\n".join(lines)


def _resolve_category(structured: dict | None, state: AgentState) -> str:
    summary = (structured or {}).get("symptoms_summary") or state.get("symptoms_summary") or ""
    if _infer_category_from_text(summary) == "CAT-03":
        # Business rule: các case niềng/chỉnh nha ưu tiên CAT-03 cho flow hiện tại.
        return "CAT-03"
    raw = (structured or {}).get("category_code")
    if isinstance(raw, str) and raw.strip().upper() in _VALID_CATEGORY_CODES:
        return raw.strip().upper()
    tags = structured.get("extracted_symptom_tags") if isinstance(structured, dict) else None
    if isinstance(tags, list) and tags:
        from app.services.triage_rubric_loader import score_category_from_symptom_tags
        tag_strs = [str(t).strip() for t in tags if str(t).strip()]
        scored = score_category_from_symptom_tags(tag_strs)
        if scored:
            return scored
    return _infer_category_from_text(summary)


def _normalize_for_match(text: str) -> str:
    s = (text or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d")
    return re.sub(r"\s+", " ", s).strip()


def _slot_question_topic(question: str) -> str:
    q = _normalize_for_match(question)
    if any(k in q for k in ("vi tri", "ham tren", "ham duoi", "phia truoc", "trong cung")):
        return "location"
    if any(k in q for k in ("bao lau", "tu bao lau", "thoi gian", "keo dai")):
        return "duration"
    if any(k in q for k in ("nong/lanh", "nong", "lanh", "ngot", "kich thich", "an/uong", "tu phat")):
        return "trigger"
    if any(k in q for k in ("sung", "mu", "sot", "sung ma", "sung nuou")):
        return "swelling_fever"
    if any(k in q for k in ("thuoc giam dau", "da uong thuoc", "co bot khong")):
        return "medication_response"
    if any(k in q for k in ("da tung tram", "tram", "chua tuy", "x-quang", "x quang", "dieu tri do dang")):
        return "treatment_history"
    if any(k in q for k in ("muc do", "1-10", "1 den 10")):
        return "severity"
    if any(k in q for k in ("be bao nhieu tuoi", "bao nhieu tuoi", "tre em", "be")):
        return "age"
    if any(k in q for k in ("rang sua", "rang vinh vien")):
        return "tooth_type"
    if any(k in q for k in ("hop tac", "khong hop tac")):
        return "cooperation"
    if any(k in q for k in ("chong dong", "benh nen", "mang thai")):
        return "comorbidity"
    if any(k in q for k in ("kham nha khoa gan nhat", "lan kham")):
        return "last_visit"
    return "other"


_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "location": ("vi tri", "rang so", "ham tren", "ham duoi", "rang trong cung", "phia truoc", "phia sau"),
    "duration": ("bao lau", "tu qua", "tu hom", "keo dai", "duoc may ngay", "may tuan"),
    "trigger": ("lanh", "nong", "ngot", "nhai", "an uong", "tu phat", "kich thich"),
    "swelling_fever": ("sung", "mu", "sot", "sung ma", "sung nuou", "sung mat"),
    "medication_response": ("thuoc giam dau", "da uong", "uống thuoc", "co bot", "do bot"),
    "treatment_history": ("da tram", "tram", "chua tuy", "nho rang", "x quang", "dieu tri", "rang khon"),
    "severity": ("muc do", "/10", "thang diem", "rat dau", "dau du doi"),
    "age": ("tuoi", "be", "chau", "tre"),
    "tooth_type": ("rang sua", "rang vinh vien"),
    "cooperation": ("hop tac", "khong cho kham", "so nha si"),
    "comorbidity": ("benh nen", "mang thai", "chong dong", "tieu duong", "huyet ap"),
    "last_visit": ("lan kham", "kham gan nhat", "dinh ky"),
}


def _topic_is_covered(topic: str, seen_blob: str) -> bool:
    kws = _TOPIC_KEYWORDS.get(topic, ())
    return any(k in seen_blob for k in kws)


def _build_targeted_missing_questions_filtered(
    category_code: str | None,
    state: AgentState,
    last_human: str,
    *,
    limit: int = 3,
) -> str:
    if not category_code:
        return "  - (chưa xác định)"
    from app.services.triage_rubric_loader import get_category_entries

    code = str(category_code).strip().upper()
    if code not in _VALID_CATEGORY_CODES:
        return "  - (chưa xác định)"

    # Gom ngữ cảnh đã biết để tránh hỏi trùng.
    seen_parts: list[str] = []
    for msg in state.get("messages") or []:
        if getattr(msg, "type", None) != "human":
            continue
        c = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        if c.strip():
            seen_parts.append(c)
    if state.get("symptoms_summary"):
        seen_parts.append(str(state["symptoms_summary"]))
    if last_human.strip():
        seen_parts.append(last_human.strip())
    seen_blob = _normalize_for_match(" | ".join(seen_parts))

    for cat in get_category_entries():
        c = str(cat.get("code") or "").strip().upper()
        if c != code:
            continue
        slots = [str(s).strip() for s in (cat.get("typical_missing_slots_to_ask") or []) if str(s).strip()]
        if not slots:
            return "  - (không có trong rubric)"

        picked: list[str] = []
        fallback: list[str] = []
        for q in slots:
            topic = _slot_question_topic(q)
            if topic == "other":
                fallback.append(q)
                continue
            if not _topic_is_covered(topic, seen_blob):
                picked.append(q)
            else:
                fallback.append(q)

        if len(picked) < limit:
            for q in fallback:
                if q not in picked:
                    picked.append(q)
                if len(picked) >= limit:
                    break
        if not picked:
            picked = slots[: max(1, limit)]

        return "\n".join(f"  - {q}" for q in picked[: max(1, limit)])
    return "  - (không có trong rubric)"


def _suspected_category_for_follow_up(state: AgentState, last_human: str) -> str | None:
    state_cat = state.get("category_code")
    if isinstance(state_cat, str) and state_cat.strip().upper() in _VALID_CATEGORY_CODES:
        return state_cat.strip().upper()

    combined = " ".join(
        p for p in [str(state.get("symptoms_summary") or "").strip(), (last_human or "").strip()] if p
    ).strip()
    if not combined:
        return None
    inferred = _infer_category_from_text(combined)
    return inferred if inferred in _VALID_CATEGORY_CODES else None


async def dental_specialist_node(state: AgentState, config: RunnableConfig) -> dict:
    llm = get_specialist_llm()
    session_id = str(state["session_id"])
    user_id = str(state["patient_user_id"])
    callbacks = get_langfuse_callback(
        config,
        trace_name="03.specialist.triage",
        session_id=session_id,
        user_id=user_id,
        trace_id=build_session_trace_id(session_id),
        metadata={
            "agent": "specialist",
            "node": "dental_specialist",
            "follow_up_count": state.get("follow_up_count", 0),
            "max_follow_ups": settings.MAX_FOLLOW_UP_QUESTIONS,
        },
        tags=["specialist", "triage", "symptom-intake"],
    )

    follow_up_count = state.get("follow_up_count", 0)
    force_conclusion = follow_up_count >= settings.MAX_FOLLOW_UP_QUESTIONS

    last_human = _last_human_content(state)
    n_humans = sum(1 for m in state.get("messages") or [] if getattr(m, "type", None) == "human")

    if _looks_like_category_count_question(last_human):
        msg = _build_mock_categories_reply()
        return {
            "messages": [AIMessage(content=msg, name="specialist_agent")],
            "last_agent_message": msg,
            "current_agent": "specialist",
            "follow_up_count": follow_up_count + 1,
            "specialist_concluded": False,
            "extra": {"message_ui": None},
        }

    if _user_signals_no_more_symptoms(last_human) and n_humans >= 2:
        summary = _build_symptoms_summary_when_closing(state, last_human)
        if not summary.strip():
            summary = "Bệnh nhân không bổ sung thêm triệu chứng qua chat."
        cat = _infer_category_from_text(summary)
        display = (
            "Cảm ơn bạn đã cung cấp thông tin. "
            "Mình sẽ chuyển bạn sang bước **chọn ngày và giờ** khám phù hợp."
        )
        logger.info(
            "[agent:specialist] early close (no more symptoms) session_id=%s category=%s",
            state["session_id"], cat,
        )
        return {
            "messages": [AIMessage(content=display, name="specialist_agent")],
            "last_agent_message": display,
            "current_agent": "specialist",
            "symptoms_summary": summary,
            "ai_diagnosis": "Không cung cấp tư vấn y khoa qua chat. Cần bác sĩ khám trực tiếp.",
            "specialist_concluded": True,
            "category_code": cat,
            # Reset booking-related fields to avoid stale state from Redis checkpoints.
            "available_slots": [],
            "pending_booking_date_iso": None,
            "pending_confirmation_slot": None,
            "booking_confirmed": False,
            "reservation_id": None,
            "selected_slot": None,
            "follow_up_count": follow_up_count + 1,
            "extra": {"message_ui": None},
        }

    suspected_category = _suspected_category_for_follow_up(state, last_human)
    targeted_questions = _build_targeted_missing_questions_filtered(
        suspected_category,
        state,
        last_human,
        limit=3,
    )
    system_content = _SPECIALIST_SYSTEM.format(
        symptoms_so_far=state.get("symptoms_summary") or "chưa có",
        suspected_category=suspected_category or "chưa xác định",
        targeted_missing_questions=targeted_questions,
        follow_up_count=follow_up_count,
        max_follow_ups=settings.MAX_FOLLOW_UP_QUESTIONS,
        force_conclusion=force_conclusion,
        rubric_excerpt=format_rubric_prompt_excerpt(),
    )
    if force_conclusion:
        system_content += (
            "\n\n**BẮT BUỘC (lượt này):** Đã đạt giới hạn câu hỏi. "
            "KHÔNG hỏi thêm. Viết cảm ơn ngắn rồi thêm ```json``` với đủ trường."
        )
    else:
        system_content += (
            "\n\n**BẮT BUỘC (lượt follow-up này):** "
            "Khi chưa kết luận, hãy hỏi theo danh sách 'Câu hỏi ưu tiên từ rubric lượt này' ở trên. "
            "Ít nhất dùng 1 ý; có thể gộp 2-3 câu ngắn trong cùng một tin nhắn."
        )

    history = list(state["messages"])
    prompt = [SystemMessage(content=system_content)] + history

    logger.info(
        "[agent:specialist] LLM invoke start session_id=%s follow_up=%s/%s prompt_msgs=%s",
        state["session_id"], follow_up_count, settings.MAX_FOLLOW_UP_QUESTIONS, len(prompt),
    )
    logger.info(
        "[agent:specialist] PROMPT session_id=%s\n\n%s\n",
        state["session_id"],
        _format_prompt_for_log(prompt),
    )
    t0 = time.monotonic()
    response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    logger.info(
        "[agent:specialist] LLM RESPONSE session_id=%s\n\n%s\n",
        state["session_id"],
        format_llm_response_for_log(response),
    )
    response_text = message_content_as_text(response.content)
    logger.info(
        "[agent:specialist] LLM done session_id=%s elapsed_s=%.2f reply_len=%s has_json=%s",
        state["session_id"], time.monotonic() - t0, len(response_text),
        "```json" in response_text.lower(),
    )

    structured = _extract_json_block(response_text)
    display_text = _strip_json_fences_for_display(response_text)
    if not display_text and structured:
        display_text = "Cảm ơn bạn. Chúng tôi đã ghi nhận và sẽ chuyển sang bước chọn lịch khám."
    elif not display_text:
        display_text = response_text.strip() or "Không nhận được phản hồi. Vui lòng gửi lại."

    updates: dict = {
        "messages": [AIMessage(content=display_text, name="specialist_agent")],
        "last_agent_message": display_text,
        "current_agent": "specialist",
        "follow_up_count": follow_up_count + 1,
        "specialist_concluded": False,
        "extra": {"message_ui": None},
    }

    if structured:
        updates["symptoms_summary"] = structured.get("symptoms_summary", state.get("symptoms_summary"))
        updates["ai_diagnosis"] = structured.get("ai_diagnosis")
        updates["specialist_concluded"] = True
        updates["category_code"] = _resolve_category(structured, state)
        logger.info("[specialist] concluded category=%s", updates["category_code"])
    elif force_conclusion:
        summary_fb = state.get("symptoms_summary") or _build_symptoms_summary_when_closing(state, last_human)
        updates["symptoms_summary"] = summary_fb
        updates["specialist_concluded"] = True
        updates["ai_diagnosis"] = "Không tư vấn y khoa qua chat. Vui lòng đến khám trực tiếp."
        updates["category_code"] = _infer_category_from_text(summary_fb)
        logger.info("[specialist] force_conclusion triggered category=%s", updates["category_code"])

    # Reset booking-related fields to avoid stale values coming from Redis checkpoints.
    # This ensures after triage we always ask patient for day(s) and then time slots.
    if updates.get("specialist_concluded"):
        updates.update(
            {
                "available_slots": [],
                "pending_booking_date_iso": None,
                "pending_confirmation_slot": None,
                "booking_confirmed": False,
                "reservation_id": None,
                "selected_slot": None,
            }
        )

    return updates
