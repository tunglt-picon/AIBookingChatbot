"""Tiện ích format nội dung LLM để ghi log (LangChain messages / multimodal)."""

from __future__ import annotations

import json
from typing import Any

__all__ = ["message_content_as_text", "format_llm_response_for_log"]

# Tránh làm phình log quá lớn (vẫn đủ cho hầu hết reply + JSON triage).
_MAX_LOG_CHARS = 32_000


def message_content_as_text(content: Any) -> str:
    """
    Chuyển `message.content` của LangChain (str | list block | dict) thành một chuỗi text.
    Hỗ trợ block dạng OpenAI: [{"type": "text", "text": "..."}, ...].
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "".join(parts)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def format_llm_response_for_log(response: Any) -> str:
    """
    Chuỗi nhiều dòng mô tả phản hồi model (content, metadata, tool_calls) phù hợp cho logger.
    `response` thường là AIMessage / BaseMessage sau `llm.ainvoke(...)`.
    """
    lines: list[str] = []
    role = getattr(response, "type", type(response).__name__)
    name = getattr(response, "name", "") or ""
    lines.append(f"type={role} name={name or '-'}")

    raw_content = getattr(response, "content", response)
    full_text = message_content_as_text(raw_content)
    text = full_text
    if len(text) > _MAX_LOG_CHARS:
        text = (
            text[:_MAX_LOG_CHARS]
            + f"\n\n...(truncated, original_len={len(full_text)})"
        )
    lines.append("content:")
    lines.append(text)

    meta = getattr(response, "response_metadata", None)
    if meta:
        lines.append("response_metadata:")
        try:
            lines.append(json.dumps(meta, ensure_ascii=False, default=str))
        except TypeError:
            lines.append(repr(meta))

    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls:
        lines.append("tool_calls:")
        try:
            lines.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
        except TypeError:
            lines.append(repr(tool_calls))

    add_kw = getattr(response, "additional_kwargs", None)
    if isinstance(add_kw, dict) and add_kw:
        lines.append("additional_kwargs:")
        try:
            lines.append(json.dumps(add_kw, ensure_ascii=False, default=str))
        except TypeError:
            lines.append(repr(add_kw))

    return "\n".join(lines)
