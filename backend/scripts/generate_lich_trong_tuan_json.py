#!/usr/bin/env python3
"""
Sinh file mock: lich_trong_tuan_trong_vi.json
Tuần 27/04/2026 (Thứ 2) → 01/05/2026 (Thứ 6)

Chạy: python3 scripts/generate_lich_trong_tuan_json.py
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.dental_cases import (
    CATEGORIES,
    build_slot_dict,
    valid_start_minutes_for_category,
)

VI_DAYS = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN")

WEEK_START = date(2026, 4, 27)
WEEK_DAYS = 5

# CAT-05 dùng bước 20p từ 13:30 → không có mốc 15:00 theo lưới; thêm một khung cho benchmark intent-08.
_EXTRA_CAT05_INTENT08_DAY = date(2026, 4, 30)
_EXTRA_CAT05_INTENT08_START_MINUTES = 15 * 60


def _inject_cat05_slot_benchmark_eval(days_out: list) -> None:
    """Chèn 15:00; bỏ mốc 14:50 cùng ngày để không chồng ca 20p."""
    for day in days_out:
        if date.fromisoformat(day["date_iso"]) != _EXTRA_CAT05_INTENT08_DAY:
            continue
        base = datetime(
            _EXTRA_CAT05_INTENT08_DAY.year,
            _EXTRA_CAT05_INTENT08_DAY.month,
            _EXTRA_CAT05_INTENT08_DAY.day,
            tzinfo=timezone.utc,
        )
        lst = day["theo_loai_kham"]["CAT-05"]
        lst[:] = [
            s for s in lst if s.get("time_hm") not in ("14:50", "15:10")
        ]
        if any(s.get("time_hm") == "15:00" for s in lst):
            break
        lst.append(build_slot_dict(base, _EXTRA_CAT05_INTENT08_START_MINUTES, "CAT-05"))
        lst.sort(key=lambda s: s["datetime_str"])
        break


def main():
    days_out = []

    for i in range(WEEK_DAYS):
        d = WEEK_START + timedelta(days=i)
        base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        is_working = d.weekday() < 5

        by_category = {}
        for code in sorted(CATEGORIES.keys()):
            if is_working:
                starts = valid_start_minutes_for_category(d, code)
                slots = [build_slot_dict(base, sm, code) for sm in starts]
            else:
                slots = []
            by_category[code] = slots

        days_out.append({
            "date_iso": d.isoformat(),
            "python_weekday": d.weekday(),
            "ten_thu_vi": VI_DAYS[d.weekday()] if d.weekday() < len(VI_DAYS) else "",
            "la_ngay_lam_viec_phong_kham": is_working,
            "theo_loai_kham": by_category,
        })

    _inject_cat05_slot_benchmark_eval(days_out)

    payload = {
        "meta": {
            "tuan_bat_dau_iso": WEEK_START.isoformat(),
            "so_ngay": WEEK_DAYS,
            "mo_ta": (
                "Lịch trống mock 5 ngày làm việc (Thứ 2–6); "
                "slot theo từng mã category (CAT-01 → CAT-05). "
                "Một số slot đã 'đặt' (bị loại) để mô phỏng thực tế."
            ),
            "ngon_ngu": "vi-VN",
            "nguon_sinh": "scripts/generate_lich_trong_tuan_json.py",
        },
        "ngay": days_out,
    }

    out_path = Path(__file__).resolve().parents[1] / "data" / "mock" / "lich_trong_tuan_trong_vi.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    total = sum(
        len(slots)
        for day in days_out
        for slots in day["theo_loai_kham"].values()
    )
    print(f"Wrote {out_path}")
    print(f"  {WEEK_DAYS} days, {len(CATEGORIES)} categories, {total} total slots")
    for day in days_out:
        counts = {k: len(v) for k, v in day["theo_loai_kham"].items()}
        print(f"  {day['date_iso']} ({day['ten_thu_vi']}): {counts}")


if __name__ == "__main__":
    main()
