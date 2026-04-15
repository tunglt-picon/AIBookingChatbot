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
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.llm_factory import get_specialist_llm
from app.config import settings
from app.observability.langfuse_client import get_langfuse_callback
from app.services.triage_rubric_loader import format_rubric_prompt_excerpt

logger = logging.getLogger(__name__)


_SPECIALIST_SYSTEM = """\
Bạn là AI tiếp nhận thông tin đặt lịch khám nha khoa qua **chat văn bản**.

Nhiệm vụ:
1. Thu thập thông tin triệu chứng bằng câu hỏi ngắn, cụ thể, mỗi lần MỘT câu.
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
- Nếu BN nói "không còn gì thêm", "hết rồi", "đủ rồi" → **chốt JSON ngay**, không hỏi thêm.
- TUYỆT ĐỐI KHÔNG tư vấn y khoa, không chẩn đoán, không đề xuất điều trị.
- Khi chốt: viết lời thoại trước, **sau đó** thêm khối ```json``` ở CUỐI.
  Lời thoại nêu rõ **tên category** (tiếng Việt) và hỏi BN xác nhận.
  Nếu 2 category gần giống → nêu cả 2 cho BN chọn.
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


def _infer_category_from_text(text: str) -> str:
    from app.domain.dental_cases import DEFAULT_CATEGORY_CODE

    low = (text or "").lower()
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


def _resolve_category(structured: dict | None, state: AgentState) -> str:
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
    summary = (structured or {}).get("symptoms_summary") or state.get("symptoms_summary") or ""
    return _infer_category_from_text(summary)


async def dental_specialist_node(state: AgentState, config: RunnableConfig) -> dict:
    llm = get_specialist_llm()
    callbacks = get_langfuse_callback(config)

    follow_up_count = state.get("follow_up_count", 0)
    force_conclusion = follow_up_count >= settings.MAX_FOLLOW_UP_QUESTIONS

    last_human = _last_human_content(state)
    n_humans = sum(1 for m in state.get("messages") or [] if getattr(m, "type", None) == "human")

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
            "follow_up_count": follow_up_count + 1,
            "extra": {"message_ui": None},
        }

    system_content = _SPECIALIST_SYSTEM.format(
        symptoms_so_far=state.get("symptoms_summary") or "chưa có",
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

    history = list(state["messages"])
    prompt = [SystemMessage(content=system_content)] + history

    logger.info(
        "[agent:specialist] LLM invoke start session_id=%s follow_up=%s/%s prompt_msgs=%s",
        state["session_id"], follow_up_count, settings.MAX_FOLLOW_UP_QUESTIONS, len(prompt),
    )
    t0 = time.monotonic()
    response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    response_text = response.content if isinstance(response.content, str) else str(response.content)
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

    return updates
