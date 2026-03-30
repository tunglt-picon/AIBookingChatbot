"""
Dental specialist agent (VLM / configurable).

Responsibilities:
  • Handle multimodal input (optional dental image + text).
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

logger = logging.getLogger(__name__)

# LLM system prompt is Vietnamese for the default patient-facing UI locale.

_SPECIALIST_SYSTEM = """\
Bạn là AI tiếp nhận thông tin đặt lịch khám nha khoa đa phương thức (multi-modal).

Nhiệm vụ của bạn:
1. Nếu có ảnh → chỉ ghi nhận mô tả quan sát ở mức hành chính (không kết luận bệnh).
2. Thu thập thông tin triệu chứng bằng câu hỏi ngắn, cụ thể, mỗi lần một câu.
3. Khi đủ thông tin hoặc đã hỏi đủ số lần, chốt thông tin để chuyển sang bước đặt lịch.

Quy tắc:
- Luôn trả lời bằng tiếng Việt.
- Hỏi tập trung vào: thời gian đau, mức độ đau (1–10), vị trí, yếu tố kích thích.
- TUYỆT ĐỐI KHÔNG tư vấn y khoa, không chẩn đoán, không đề xuất điều trị.
- Khi kết luận: viết lời thoại cho bệnh nhân trước, sau đó XUỐNG DÒNG và thêm khối JSON ở CUỐI.
  Khối JSON chỉ để hệ thống đọc — bệnh nhân sẽ không thấy nó, nên lời thoại phải đầy đủ ý.
- Mẫu JSON (đặt sau lời thoại):

```json
{{
  "symptoms_summary": "Tóm tắt triệu chứng",
  "ai_diagnosis": "Không cung cấp tư vấn y khoa. Cần bác sĩ khám trực tiếp.",
  "needs_visit": true
}}
```

Thông tin hiện tại:
  - Triệu chứng đã thu thập: {symptoms_so_far}
  - Số câu hỏi đã hỏi: {follow_up_count}/{max_follow_ups}
  - Bắt buộc kết luận: {force_conclusion}
"""


def _build_multimodal_message(text: str, image_base64: str, mime_type: str) -> HumanMessage:
    """Wrap text + base64 image into a LangChain multimodal HumanMessage."""
    return HumanMessage(content=[
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{image_base64}",
                "detail": "high",
            },
        },
        {"type": "text", "text": text},
    ])


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


async def dental_specialist_node(state: AgentState, config: RunnableConfig) -> dict:
    llm = get_specialist_llm()
    callbacks = get_langfuse_callback(config)

    follow_up_count = state.get("follow_up_count", 0)
    force_conclusion = follow_up_count >= settings.MAX_FOLLOW_UP_QUESTIONS

    system_content = _SPECIALIST_SYSTEM.format(
        symptoms_so_far=state.get("symptoms_summary") or "chưa có",
        follow_up_count=follow_up_count,
        max_follow_ups=settings.MAX_FOLLOW_UP_QUESTIONS,
        force_conclusion=force_conclusion,
    )

    # Build message history – inject image into the first human message if available
    history = list(state["messages"])
    image_b64 = state.get("image_base64")
    image_mime = state.get("image_mime_type", "image/jpeg")

    if image_b64 and history:
        # Replace the last human message with a multimodal one
        for i in range(len(history) - 1, -1, -1):
            if hasattr(history[i], "type") and history[i].type == "human":
                original_text = (
                    history[i].content
                    if isinstance(history[i].content, str)
                    else str(history[i].content)
                )
                history[i] = _build_multimodal_message(original_text, image_b64, image_mime)
                break

    prompt = [SystemMessage(content=system_content)] + history

    logger.info(
        "[agent:specialist] LLM invoke start session_id=%s follow_up=%s/%s multimodal=%s prompt_msgs=%s",
        state["session_id"],
        follow_up_count,
        settings.MAX_FOLLOW_UP_QUESTIONS,
        bool(image_b64),
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
        logger.info(
            f"[specialist] diagnosis='{updates['ai_diagnosis']}' "
            f"needs_visit={updates['needs_visit']}"
        )
    elif force_conclusion:
        # Force-set needs_visit when max follow-ups reached even without JSON
        updates["needs_visit"] = True
        updates["ai_diagnosis"] = (
            "Không cung cấp tư vấn y khoa qua chat. Vui lòng đến khám trực tiếp để bác sĩ đánh giá."
        )
        logger.info("[specialist] force_conclusion triggered")

    return updates
