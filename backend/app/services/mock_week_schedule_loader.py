"""
Đọc dữ liệu lịch trống mock cả tuần từ JSON.

File: backend/data/mock/lich_trong_tuan_trong_vi.json
Tạo lại: cd backend && python3 scripts/generate_lich_trong_tuan_json.py
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _json_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "mock" / "lich_trong_tuan_trong_vi.json"


@lru_cache(maxsize=1)
def load_week_mock_raw() -> dict[str, Any]:
    path = _json_path()
    if not path.is_file():
        logger.warning("[mock_week] missing file %s", path)
        return {"meta": {}, "ngay": []}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def list_mock_date_isos() -> list[str]:
    out: list[str] = []
    for day in load_week_mock_raw().get("ngay") or []:
        di = day.get("date_iso")
        if isinstance(di, str) and len(di) >= 10:
            out.append(di.strip()[:10])
    return out


def first_mock_date_iso_for_category(category_code: Optional[str]) -> str:
    from app.domain.dental_cases import normalize_category_code

    code = normalize_category_code(category_code)
    data = load_week_mock_raw()
    for day in data.get("ngay") or []:
        di = (day.get("date_iso") or "")[:10]
        if not di:
            continue
        slots = (day.get("theo_loai_kham") or {}).get(code) or []
        if slots:
            return di
    meta = data.get("meta") or {}
    start = (meta.get("tuan_bat_dau_iso") or "")[:10]
    if start:
        return start
    for day in data.get("ngay") or []:
        raw = day.get("date_iso")
        if isinstance(raw, str) and len(raw) >= 10:
            return raw.strip()[:10]
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def mock_schedule_summary_for_lab() -> dict[str, Any]:
    data = load_week_mock_raw()
    meta = dict(data.get("meta") or {})
    days_out: list[dict[str, Any]] = []
    for day in data.get("ngay") or []:
        di = (day.get("date_iso") or "")[:10]
        by_case = day.get("theo_loai_kham") or {}
        counts = {k: len(v) if isinstance(v, list) else 0 for k, v in by_case.items()}
        days_out.append({
            "date_iso": di,
            "ten_thu_vi": day.get("ten_thu_vi"),
            "la_ngay_lam_viec_phong_kham": day.get("la_ngay_lam_viec_phong_kham"),
            "so_slot_theo_loai": counts,
        })
    return {
        "tep_json": "lich_trong_tuan_trong_vi.json",
        "meta": meta,
        "cac_ngay": days_out,
        "cac_ma_loai_kham_trong_file": sorted(
            {k for d in data.get("ngay") or [] for k in (d.get("theo_loai_kham") or {}).keys()}
        ),
    }


def get_mock_slots_for_date_and_category(
    date_iso: str,
    category_code: Optional[str],
    limit: int = 12,
) -> list[dict[str, Any]]:
    from app.domain.dental_cases import normalize_category_code

    code = normalize_category_code(category_code)
    key = date_iso.strip()[:10]
    for day in load_week_mock_raw().get("ngay") or []:
        if day.get("date_iso") == key:
            slots = (day.get("theo_loai_kham") or {}).get(code) or []
            return list(slots[:limit])
    return []


def build_week_availability_payload(
    category_code: Optional[str] = None,
    week_start_iso: Optional[str] = None,
) -> dict[str, Any]:
    from app.domain.dental_cases import normalize_category_code

    data = load_week_mock_raw()
    meta = dict(data.get("meta") or {})
    expected_start = (meta.get("tuan_bat_dau_iso") or "")[:10]
    if week_start_iso and week_start_iso.strip()[:10] != expected_start:
        return {
            "ok": False,
            "loi": (
                f"Dữ liệu mock chỉ có tuần bắt đầu {expected_start}. "
                f"Bạn yêu cầu {week_start_iso.strip()[:10]}."
            ),
            "meta": meta,
            "ngay": [],
        }

    code_filter = normalize_category_code(category_code) if category_code else None
    days_out: list[dict[str, Any]] = []

    for day in data.get("ngay") or []:
        entry: dict[str, Any] = {
            "date_iso": day.get("date_iso"),
            "ten_thu_vi": day.get("ten_thu_vi"),
            "la_ngay_lam_viec_phong_kham": day.get("la_ngay_lam_viec_phong_kham"),
        }
        by_case = day.get("theo_loai_kham") or {}
        if code_filter:
            entry["slots"] = by_case.get(code_filter) or []
            entry["category_code"] = code_filter
        else:
            entry["theo_loai_kham"] = by_case
        days_out.append(entry)

    return {
        "ok": True,
        "meta": meta,
        "ngay": days_out,
    }
