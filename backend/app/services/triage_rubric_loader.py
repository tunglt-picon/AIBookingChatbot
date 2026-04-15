"""
Tải rubric triage: category ↔ tín hiệu triệu chứng ↔ gợi ý câu hỏi + ma trận.

File: backend/data/mock/triage_symptom_rubric_vi.json
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VALID_CODES = frozenset({"CAT-01", "CAT-02", "CAT-03", "CAT-04", "CAT-05"})


def _rubric_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "mock" / "triage_symptom_rubric_vi.json"


def invalidate_triage_rubric_cache() -> None:
    load_triage_rubric_raw.cache_clear()


@lru_cache(maxsize=1)
def load_triage_rubric_raw() -> dict[str, Any]:
    path = _rubric_path()
    if not path.is_file():
        logger.warning("[triage_rubric] missing file %s", path)
        return {"schema_version": 0, "categories": [], "symptom_matrix": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_category_entries() -> list[dict[str, Any]]:
    data = load_triage_rubric_raw()
    return list(data.get("categories") or [])


def get_symptom_matrix() -> list[dict[str, Any]]:
    data = load_triage_rubric_raw()
    return list(data.get("symptom_matrix") or [])


def format_rubric_prompt_excerpt(
    *,
    max_chars: int = 14000,
) -> str:
    """
    Đoạn văn bản đưa vào system prompt specialist: định nghĩa category + ma trận triệu chứng mẫu.
    """
    data = load_triage_rubric_raw()
    lines: list[str] = []
    lines.append("=== BẢNG THAM CHIẾU PHÂN LOẠI (5 category — chỉ để xếp loại lịch khám) ===")

    for cat in data.get("categories") or []:
        code = cat.get("code", "")
        label = cat.get("label_vi", "")
        desc = cat.get("description_vi", "")
        sig = cat.get("symptom_signals") or []
        slots = cat.get("typical_missing_slots_to_ask") or []
        boundary = cat.get("boundary_notes_vi") or ""
        lines.append(f"\n• {code} — {label}")
        if desc:
            lines.append(f"  Mô tả: {desc}")
        if boundary:
            lines.append(f"  Phân biệt: {boundary}")
        if sig:
            lines.append("  Tín hiệu: " + "; ".join(sig[:20]))
        if slots:
            lines.append("  Cần hỏi thêm: " + " | ".join(slots[:8]))

    matrix = data.get("symptom_matrix") or []
    if matrix:
        lines.append("\n=== MẪU TRIỆU CHỨNG → CATEGORY (trích 5 mẫu mỗi nhóm) ===")
        by_cat: dict[str, list[str]] = {}
        for row in matrix:
            cat = row.get("category", "")
            text = row.get("text", "")
            by_cat.setdefault(cat, []).append(text)
        for cat_code in sorted(by_cat.keys()):
            samples = by_cat[cat_code][:5]
            lines.append(f"\n[{cat_code}]")
            for s in samples:
                lines.append(f"  - {s}")

    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[: max_chars - 20] + "\n…[cắt bớt]…"
    return out


def score_category_from_symptom_tags(tags: list[str]) -> Optional[str]:
    """
    Heuristic: cộng điểm theo symptom_signals từng category.
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
