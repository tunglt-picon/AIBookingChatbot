"""
Tải rubric triage: category ↔ tín hiệu triệu chứng ↔ gợi ý câu hỏi + ví dụ có nhãn.

File: backend/data/mock/triage_symptom_rubric_vi.json
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VALID_CODES = frozenset({"CAVITY", "IMPLANT", "GINGIVITIS", "SCALING", "EMERGENCY"})


def _rubric_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "mock" / "triage_symptom_rubric_vi.json"


def invalidate_triage_rubric_cache() -> None:
    load_triage_rubric_raw.cache_clear()


@lru_cache(maxsize=1)
def load_triage_rubric_raw() -> dict[str, Any]:
    path = _rubric_path()
    if not path.is_file():
        logger.warning("[triage_rubric] missing file %s", path)
        return {"schema_version": 0, "categories": [], "examples": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_category_entries() -> list[dict[str, Any]]:
    data = load_triage_rubric_raw()
    return list(data.get("categories") or [])


def get_triage_examples() -> list[dict[str, Any]]:
    data = load_triage_rubric_raw()
    return list(data.get("examples") or [])


def format_rubric_prompt_excerpt(
    *,
    max_example_rows: int = 12,
    max_chars: int = 12000,
) -> str:
    """
    Đoạn văn bản đưa vào system prompt specialist: định nghĩa category + ví dụ rút gọn.
    """
    data = load_triage_rubric_raw()
    lines: list[str] = []
    lines.append("=== BẢNG THAM CHIẾU PHÂN LOẠI (mock — chỉ để xếp loại lịch khám) ===")
    for cat in data.get("categories") or []:
        code = cat.get("code", "")
        label = cat.get("label_vi", "")
        sig = cat.get("symptom_signals") or []
        slots = cat.get("typical_missing_slots_to_ask") or []
        boundary = cat.get("boundary_notes_vi") or ""
        lines.append(f"\n• {code} — {label}")
        if boundary:
            lines.append(f"  Phân biệt: {boundary}")
        if sig:
            lines.append("  Tín hiệu triệu chứng (khớp một phần là đủ gợi ý): " + "; ".join(sig[:20]))
        if slots:
            lines.append("  Thường cần hỏi thêm: " + " | ".join(slots[:8]))

    ex = data.get("examples") or []
    if ex:
        lines.append("\n=== VÍ DỤ: tin nhắn → triệu chứng bóc tách → câu hỏi thiếu → mã gợi ý ===")
        for i, row in enumerate(ex[:max_example_rows]):
            um = (row.get("user_message") or "")[:180]
            es = row.get("extracted_symptoms") or []
            ms = row.get("missing_slots_questions") or ""
            sc = row.get("suggested_case_code") or ""
            es_s = ", ".join(es[:12]) if isinstance(es, list) else str(es)
            lines.append(f"\n[{i + 1}] BN: {um}")
            lines.append(f"    Triệu chứng (tags): {es_s}")
            lines.append(f"    Hỏi thêm: {ms[:220]}")
            lines.append(f"    → dental_case_code gợi ý: {sc}")

    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[: max_chars - 20] + "\n…[cắt bớt, còn ví dụ trong file JSON]…"
    return out


def score_case_from_symptom_tags(tags: list[str]) -> Optional[str]:
    """
    Heuristic đơn giản: cộng điểm theo symptom_signals từng category (fallback khi LLM thiếu mã).
    """
    if not tags:
        return None
    blob = " ".join(t.lower() for t in tags if isinstance(t, str))
    scores: dict[str, int] = {c: 0 for c in _VALID_CODES}
    for cat in get_category_entries():
        code = str(cat.get("code") or "").upper()
        if code not in scores:
            continue
        for sig in cat.get("symptom_signals") or []:
            if not isinstance(sig, str):
                continue
            s = sig.lower().strip()
            if len(s) >= 2 and s in blob:
                scores[code] += 2
            elif len(s) >= 4 and any(
                part in blob for part in s.replace("/", " ").split() if len(part) >= 3
            ):
                scores[code] += 1
    best = max(scores.values())
    if best <= 0:
        return None
    for code, v in scores.items():
        if v == best:
            return code
    return None
