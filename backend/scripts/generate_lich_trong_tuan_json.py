#!/usr/bin/env python3
"""Sinh lại backend/data/mock/lich_trong_tuan_trong_vi.json từ dental_cases (đồng bộ slot)."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.dental_cases import DENTAL_CASES, slots_for_date_and_case  # noqa: E402

_WEEKDAY_VI = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật")


def main() -> None:
    # Thứ 2 làm mốc; đổi ngày này khi cần tuần demo khác rồi chạy lại script
    start = date(2026, 3, 30)
    payload = {
        "meta": {
            "tuan_bat_dau_iso": start.isoformat(),
            "so_ngay": 7,
            "mo_ta": "Lịch trống mock 7 ngày; slot theo từng mã loại khám (CAVITY, IMPLANT, …). "
            "Cuối tuần (Thứ 7, CN) danh sách rỗng — phòng không làm việc.",
            "ngon_ngu": "vi-VN",
            "nguon_sinh": "app.domain.dental_cases.slots_for_date_and_case",
        },
        "ngay": [],
    }

    for i in range(7):
        d = start + timedelta(days=i)
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        entry = {
            "date_iso": d.isoformat(),
            "python_weekday": d.weekday(),
            "ten_thu_vi": _WEEKDAY_VI[d.weekday()],
            "la_ngay_lam_viec_phong_kham": d.weekday() < 5,
            "theo_loai_kham": {},
        }
        for code in DENTAL_CASES:
            entry["theo_loai_kham"][code] = slots_for_date_and_case(dt, code, limit=50)
        payload["ngay"].append(entry)

    out = ROOT / "data" / "mock" / "lich_trong_tuan_trong_vi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
