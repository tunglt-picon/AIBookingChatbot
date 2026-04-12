"""
Dental specialist agent (text LLM / configurable).

Responsibilities:
  • Thu thập triệu chứng qua chat văn bản.
  • Ask short follow-up questions (capped by MAX_FOLLOW_UP_QUESTIONS).
  • Emit structured intake fields for booking handoff:
      - symptoms_summary
      - ai_diagnosis (non-clinical placeholder text; no medical advice)
      - needs_visit (bool)

Merging control:
  When follow_up_count reaches MAX_FOLLOW_UP_QUESTIONS the agent must conclude
  instead of asking another question, avoiding repetitive loops.
"""

import json
import logging
import re
import time
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.llm_factory import get_specialist_llm
from app.config import settings
from app.observability.langfuse_client import get_langfuse_callback
from app.services.triage_rubric_loader import format_rubric_prompt_excerpt

logger = logging.getLogger(__name__)

# LLM system prompt is Vietnamese for the default patient-facing UI locale.

_SPECIALIST_SYSTEM = """\
Bạn là AI tiếp nhận thông tin đặt lịch khám nha khoa qua **chat văn bản**.

Nhiệm vụ của bạn:
1. Thu thập thông tin triệu chứng bằng câu hỏi ngắn, cụ thể, mỗi lần một câu.
   Nếu bệnh nhân nói sẵn ngày/giờ muốn khám → ghi nhận trong lời thoại,
   nhưng **vẫn phải hỏi triệu chứng** trước khi chốt JSON (chưa đủ thông tin chỉ vì có ngày).
2. Khi đủ thông tin hoặc đã hỏi đủ số lần, chốt thông tin để chuyển sang bước đặt lịch.
3. **Phân loại nhu cầu khám** (chỉ để hệ thống xếp **thời lượng & khung giờ** phù hợp — KHÔNG thay cho chẩn đoán BS).

Quy tắc:
- Luôn trả lời bằng tiếng Việt.
- Hỏi tập trung vào: thời gian đau, mức độ đau (1–10), vị trí, yếu tố kích thích.
- Nếu bệnh nhân nói **không còn triệu chứng** / **chỉ có vậy** / **hết rồi** / **đủ rồi** → **không hỏi thêm**;
  cảm ơn ngắn và **chốt JSON trong cùng lượt** (needs_visit: true nếu đang đặt lịch).
- TUYỆT ĐỐI KHÔNG tư vấn y khoa, không chẩn đoán, không đề xuất điều trị.
- Khi kết luận: viết lời thoại cho bệnh nhân trước, sau đó XUỐNG DÒNG và thêm khối JSON ở CUỐI.
  Khối JSON chỉ để hệ thống đọc — bệnh nhân sẽ không thấy nó, nên lời thoại phải đầy đủ ý.
- Trong JSON, **dental_case_code** là MỘT trong các mã sau (chữ HOA, đúng chính tả):
  • CAVITY — sâu răng, đau theo lỗ sâu, cần trám / khắc phục sâu
  • IMPLANT — trồng răng, implant, phục hình thay răng mất lâu dài
  • GINGIVITIS — viêm nướu, chảy máu chân răng, hơi thở, nha chu
  • SCALING — cạo vôi, vệ sinh định kỳ, khám dự phòng
  • EMERGENCY — đau dữ dội, sưng mặt / nướu, áp xe, chấn thương răng cấp (cần xếp sớm)
- Chọn mã **sát nhất** với triệu chứng bệnh nhân mô tả. Nếu không rõ → SCALING.
- Luồng tham chiếu mock (bắt buộc áp dụng ý tưởng, không cần trích đúng chữ):
  1) Bóc tách **danh sách triệu chứng / tag** từ tin nhắn BN (như cột «triệu chứng bóc tách» trong rubric).
  2) So sánh với **tín hiệu** từng mã trong rubric + ví dụ mẫu → chọn `dental_case_code`.
  3) Hỏi thêm các **slot còn thiếu** (thời gian đau, mức độ, vị trí, kích thích…) giống phong cách cột «hỏi thêm» trong rubric.
  4) Khi chốt, trong **lời thoại** (không nằm trong JSON) hãy nêu rõ **nhãn loại khám** tiếng Việt tương ứng mã:
     CAVITY → «Sâu răng / khắc phục sâu răng», IMPLANT → «Trồng răng / implant»,
     GINGIVITIS → «Viêm nướu / nha chu», SCALING → «Cạo vôi / vệ sinh răng miệng»,
     EMERGENCY → «Đau cấp / sưng nướu / cần xử lý nhanh»,
     và mời BN **xác nhận một lần** có đúng nhu cầu đặt lịch theo loại đó (nếu BN bảo sai, lượt sau điều chỉnh mã cho phù hợp).

{rubric_excerpt}

- Mẫu JSON (đặt sau lời thoại):

```json
{{
  "symptoms_summary": "Tóm tắt triệu chứng",
  "extracted_symptom_tags": ["tag tiếng Việt ngắn", "…"],
  "ai_diagnosis": "Không cung cấp tư vấn y khoa. Cần bác sĩ khám trực tiếp.",
  "needs_visit": true,
  "dental_case_code": "SCALING"
}}
```
  (Trường `extracted_symptom_tags` **khuyến nghị** khi chốt — giúp so khớp rubric; có thể bỏ qua nếu đã chắc chắn `dental_case_code`.)

Thông tin hiện tại:
  - Triệu chứng đã thu thập: {symptoms_so_far}
  - Số câu hỏi đã hỏi: {follow_up_count}/{max_follow_ups}
  - Bắt buộc kết luận: {force_conclusion}
"""


def _extract_json_block(text: str) -> dict | None:
    """Extract the first ```json ... ``` block from the specialist's response."""
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _strip_json_fences_for_display(text: str) -> str:
    """Remove fenced JSON blocks; patients should not see internal structured payloads."""
    cleaned = text
    match = re.search(r"```json\s*[\s\S]+?\s*```", cleaned, flags=re.IGNORECASE)
    if match:
        cleaned = cleaned[: match.start()] + cleaned[match.end() :]
    else:
        # Generic ``` ... ``` that looks like a single JSON object
        match = re.search(r"```\s*(\{[\s\S]+?\})\s*```", cleaned)
        if match and '"symptoms_summary"' in match.group(1):
            cleaned = cleaned[: match.start()] + cleaned[match.end() :]
    cleaned = cleaned.strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _fallback_user_text_after_intake() -> str:
    return (
        "Cảm ơn bạn. Chúng tôi đã ghi nhận thông tin và sẽ chuyển sang bước chọn lịch khám."
    )


def _user_signals_no_more_symptoms(text: str) -> bool:
    """BN xác nhận không còn triệu chứng / đủ thông tin — phải chốt, không hỏi thêm."""
    low = (text or "").lower().strip()
    if not low:
        return False
    phrases = (
        "hết rồi",
        "het roi",
        "không còn",
        "khong con",
        "không có triệu chứng nào",
        "khong co trieu chung nao",
        "không có triệu chứng gì",
        "chỉ có vậy",
        "chi co vay",
        "chỉ vậy",
        "chi vay",
        "đủ rồi",
        "du roi",
        "thế thôi",
        "the thoi",
        "vậy thôi",
        "vay thoi",
        "bấy nhiêu thôi",
        "bay nhieu thoi",
        "không nữa đâu",
        "khong nua dau",
        "không ạ không có gì thêm",
        "that's all",
        "no more",
    )
    return any(p in low for p in phrases)


def _last_human_content(state: AgentState) -> str:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) == "human":
            if isinstance(msg.content, str):
                return msg.content.strip()
            return str(msg.content or "").strip()
    return ""


def _build_symptoms_summary_when_closing(state: AgentState, closing_message: str) -> str:
    if state.get("symptoms_summary"):
        return str(state["symptoms_summary"])[:2000]
    parts: list[str] = []
    for msg in state.get("messages") or []:
        if getattr(msg, "type", None) != "human":
            continue
        c = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        c = c.strip()
        if c and c != closing_message.strip():
            parts.append(c)
    return " ".join(parts)[:2000] if parts else closing_message[:500]


_VALID_CASE_CODES = frozenset({"CAVITY", "IMPLANT", "GINGIVITIS", "SCALING", "EMERGENCY"})


def _infer_case_from_text(text: str) -> str:
    """Phân loại thô từ chữ (khi LLM thiếu mã)."""
    from app.domain.dental_cases import DEFAULT_CASE_CODE

    low = (text or "").lower()
    if any(k in low for k in ("implant", "trồng răng", "cấy ghép", "cấy implant")):
        return "IMPLANT"
    if any(k in low for k in ("sâu răng", "lỗ sâu", "trám", "khoằm")):
        return "CAVITY"
    if any(k in low for k in ("nướu", "chảy máu chân răng", "viêm nướu", "nha chu", "hôi miệng")):
        return "GINGIVITIS"
    if any(k in low for k in ("cạo vôi", "vệ sinh răng", "khám định kỳ", "dự phòng")):
        return "SCALING"
    if any(k in low for k in ("đau nhức", "sưng mặt", "áp xe", "cấp cứu", "gãy răng", "va đập", "cắn phải")):
        return "EMERGENCY"
    return DEFAULT_CASE_CODE


def _resolve_dental_case(structured: dict | None, state: AgentState) -> str:
    raw = (structured or {}).get("dental_case_code")
    if isinstance(raw, str) and raw.strip().upper() in _VALID_CASE_CODES:
        return raw.strip().upper()
    summary = (structured or {}).get("symptoms_summary") or state.get("symptoms_summary") or ""
    tags = structured.get("extracted_symptom_tags") if isinstance(structured, dict) else None
    if isinstance(tags, list) and tags:
        from app.services.triage_rubric_loader import score_case_from_symptom_tags

        tag_strs = [str(t).strip() for t in tags if str(t).strip()]
        scored = score_case_from_symptom_tags(tag_strs)
        if scored:
            return scored
    return _infer_case_from_text(summary)


async def dental_specialist_node(state: AgentState, config: RunnableConfig) -> dict:
    llm = get_specialist_llm()
    callbacks = get_langfuse_callback(config)

    follow_up_count = state.get("follow_up_count", 0)
    force_conclusion = follow_up_count >= settings.MAX_FOLLOW_UP_QUESTIONS

    last_human = _last_human_content(state)
    n_humans = sum(1 for m in state.get("messages") or [] if getattr(m, "type", None) == "human")

    # BN đã nói rõ “không còn triệu chứng” / “hết rồi” → chốt ngay, tránh vòng lặp LLM không xuất JSON
    if _user_signals_no_more_symptoms(last_human) and n_humans >= 2:
        summary = _build_symptoms_summary_when_closing(state, last_human)
        if not summary.strip():
            summary = "Bệnh nhân không bổ sung thêm triệu chứng qua chat."
        case = _infer_case_from_text(summary)
        display = (
            "Cảm ơn bạn đã cung cấp thông tin. "
            "Mình sẽ chuyển bạn sang bước **chọn ngày và giờ** khám phù hợp với nhu cầu của bạn."
        )
        logger.info(
            "[agent:specialist] early close (no more symptoms) session_id=%s case=%s",
            state["session_id"],
            case,
        )
        return {
            "messages": [AIMessage(content=display, name="specialist_agent")],
            "last_agent_message": display,
            "current_agent": "specialist",
            "symptoms_summary": summary,
            "ai_diagnosis": (
                "Không cung cấp tư vấn y khoa qua chat. Cần bác sĩ khám trực tiếp."
            ),
            "needs_visit": True,
            "dental_case_code": case,
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
            "\n\n**BẮT BUỘC (lượt này):** Đã đạt giới hạn câu hỏi làm rõ. "
            "Bạn **KHÔNG** được hỏi thêm câu nào. "
            "Viết **một** đoạn cảm ơn ngắn rồi **ngay lập tức** thêm khối ```json với đủ trường "
            "(symptoms_summary tóm tắt toàn bộ triệu chứng đã nói, needs_visit: true, dental_case_code). "
            "Không lặp lại câu hỏi cũ."
        )

    history = list(state["messages"])
    prompt = [SystemMessage(content=system_content)] + history

    logger.info(
        "[agent:specialist] LLM invoke start session_id=%s follow_up=%s/%s prompt_msgs=%s",
        state["session_id"],
        follow_up_count,
        settings.MAX_FOLLOW_UP_QUESTIONS,
        len(prompt),
    )
    t0 = time.monotonic()
    response = await llm.ainvoke(prompt, config={"callbacks": callbacks})
    response_text = response.content if isinstance(response.content, str) else str(response.content)
    logger.info(
        "[agent:specialist] LLM done session_id=%s elapsed_s=%.2f reply_len=%s has_json_fence=%s",
        state["session_id"],
        time.monotonic() - t0,
        len(response_text),
        "```json" in response_text.lower(),
    )

    # ── Parse structured output from raw LLM text; show user-facing text only ──
    structured = _extract_json_block(response_text)
    display_text = _strip_json_fences_for_display(response_text)
    if not display_text and structured:
        display_text = _fallback_user_text_after_intake()
    elif not display_text:
        display_text = (
            response_text.strip()
            or "Không nhận được nội dung phản hồi. Vui lòng gửi lại tin nhắn."
        )

    updates: dict = {
        "messages": [AIMessage(content=display_text, name="specialist_agent")],
        "last_agent_message": display_text,
        "current_agent": "specialist",
        "follow_up_count": follow_up_count + 1,
        "extra": {"message_ui": None},
    }

    if structured:
        updates["symptoms_summary"] = structured.get("symptoms_summary", state.get("symptoms_summary"))
        updates["ai_diagnosis"] = structured.get("ai_diagnosis")
        updates["needs_visit"] = structured.get("needs_visit", False)
        updates["dental_case_code"] = _resolve_dental_case(structured, state)
        logger.info(
            f"[specialist] diagnosis='{updates['ai_diagnosis']}' "
            f"needs_visit={updates['needs_visit']} case={updates['dental_case_code']}"
        )
    elif force_conclusion:
        # Force-set needs_visit when max follow-ups reached even without JSON
        summary_fb = state.get("symptoms_summary") or _build_symptoms_summary_when_closing(
            state, last_human
        )
        updates["symptoms_summary"] = summary_fb
        updates["needs_visit"] = True
        updates["ai_diagnosis"] = (
            "Không cung cấp tư vấn y khoa qua chat. Vui lòng đến khám trực tiếp để bác sĩ đánh giá."
        )
        updates["dental_case_code"] = _infer_case_from_text(summary_fb)
        logger.info("[specialist] force_conclusion triggered")

    return updates
