"""
Danh mục khám nha khoa – 5 category (CAT-01 → CAT-05).

Mỗi category có:
  - Mã (code), tên tiếng Việt, mô tả chi tiết
  - Thời lượng 1 ca khám (phút)
  - Khung giờ cho phép (sáng / chiều) + bước lưới (step)

Dùng để:
  1. Phân loại triệu chứng → category
  2. Sinh lịch trống mock (hoặc đọc từ JSON)
  3. Hiển thị nhãn / mô tả trên UI
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

DEFAULT_CATEGORY_CODE = "CAT-05"

_VALID_CODES = frozenset({"CAT-01", "CAT-02", "CAT-03", "CAT-04", "CAT-05"})


@dataclass(frozen=True)
class CategoryProfile:
    code: str
    label_vi: str
    description_vi: str
    duration_minutes: int
    morning_start: str       # "HH:MM"
    morning_end: str         # ca khám phải **kết thúc** trước hoặc đúng mốc này
    afternoon_start: str
    afternoon_end: str
    step_minutes: int = 30   # bước lưới slot


CATEGORIES: dict[str, CategoryProfile] = {
    "CAT-01": CategoryProfile(
        code="CAT-01",
        label_vi="Trám răng / Phục hồi thẩm mỹ",
        description_vi=(
            "Trám răng sâu (composite / amalgam), phục hồi men răng bị mẻ hoặc sứt, "
            "trám thẩm mỹ kẽ răng, thay miếng trám cũ, xử lý mòn cổ răng. "
            "Ca khám ngắn, thường 30 phút."
        ),
        duration_minutes=30,
        morning_start="08:00",
        morning_end="12:00",
        afternoon_start="13:30",
        afternoon_end="17:00",
        step_minutes=30,
    ),
    "CAT-02": CategoryProfile(
        code="CAT-02",
        label_vi="Điều trị Tủy / Nội nha",
        description_vi=(
            "Lấy tủy, chữa tủy răng bị viêm hoặc hoại tử, điều trị áp xe chân răng, "
            "chữa tủy lại (retreatment). Ca khám dài hơn, cần 60 phút."
        ),
        duration_minutes=60,
        morning_start="08:00",
        morning_end="11:00",
        afternoon_start="13:30",
        afternoon_end="16:00",
        step_minutes=60,
    ),
    "CAT-03": CategoryProfile(
        code="CAT-03",
        label_vi="Nhổ răng / Tiểu phẫu",
        description_vi=(
            "Nhổ răng khôn (đơn giản & phẫu thuật), nhổ răng sâu nát, nhổ chân răng tồn dư, "
            "tiểu phẫu cắt lợi trùm, cắt chóp chân răng. Ưu tiên buổi sáng để theo dõi sau phẫu thuật. "
            "Thời lượng 45 phút."
        ),
        duration_minutes=45,
        morning_start="08:00",
        morning_end="11:30",
        afternoon_start="13:30",
        afternoon_end="15:30",
        step_minutes=45,
    ),
    "CAT-04": CategoryProfile(
        code="CAT-04",
        label_vi="Nha khoa Trẻ em",
        description_vi=(
            "Khám răng cho trẻ, trám răng sữa, nhổ răng sữa lung lay, bôi fluoride, "
            "trám bít hố rãnh phòng ngừa, điều trị tủy răng sữa, chấm thuốc đen giữ răng. "
            "Ca 30 phút, bác sĩ chuyên trẻ em."
        ),
        duration_minutes=30,
        morning_start="08:00",
        morning_end="11:30",
        afternoon_start="14:00",
        afternoon_end="16:30",
        step_minutes=30,
    ),
    "CAT-05": CategoryProfile(
        code="CAT-05",
        label_vi="Khám Tổng quát & X-Quang",
        description_vi=(
            "Khám định kỳ tổng quát, lấy cao răng / đánh bóng, chụp X-quang toàn cảnh, "
            "tư vấn niềng răng / implant / sứ thẩm mỹ, kiểm tra nha chu, "
            "cấp giấy khám sức khỏe răng miệng. Ca 20 phút."
        ),
        duration_minutes=20,
        morning_start="08:00",
        morning_end="12:00",
        afternoon_start="13:30",
        afternoon_end="17:00",
        step_minutes=20,
    ),
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def normalize_category_code(raw: Optional[str]) -> str:
    if raw is None or not isinstance(raw, str):
        return DEFAULT_CATEGORY_CODE
    u = raw.strip().upper()
    if u in _VALID_CODES:
        return u
    return DEFAULT_CATEGORY_CODE


def category_profile(code: Optional[str]) -> CategoryProfile:
    return CATEGORIES.get(normalize_category_code(code), CATEGORIES[DEFAULT_CATEGORY_CODE])


def category_label_vi(code: Optional[str]) -> str:
    return category_profile(code).label_vi


# Mô tả 1 câu, tập trung vào "nhóm việc sẽ làm" để BN dễ nhận ra mình có thuộc nhóm này không.
_CATEGORY_SHORT_DESC_VI: dict[str, str] = {
    "CAT-01": "xử lý răng sâu / mẻ / mòn cổ răng bằng miếng trám (thường 30 phút).",
    "CAT-02": "chữa tủy răng viêm hoặc hoại tử, xử lý áp xe chân răng (thường 60 phút).",
    "CAT-03": "nhổ răng (răng khôn, răng lung lay, chân răng) hoặc tiểu phẫu nha (khoảng 45 phút).",
    "CAT-04": "khám và điều trị răng cho trẻ em — trám sữa, nhổ sữa, bôi fluoride (30 phút).",
    "CAT-05": "khám tổng quát, lấy cao răng, chụp X-quang, tư vấn niềng / sứ / implant (20 phút).",
}


def category_short_description_vi(code: Optional[str]) -> str:
    return _CATEGORY_SHORT_DESC_VI.get(normalize_category_code(code), "").strip()


def _hm_to_minutes(hm: str) -> int:
    h, m = map(int, hm.split(":", 1))
    return h * 60 + m


def minutes_to_hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


_VI_DAYS = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN")


def _slot_display_label(d: date, time_str: str) -> str:
    wd = d.weekday()
    day_name = _VI_DAYS[wd] if wd < len(_VI_DAYS) else f"wd{wd}"
    return f"{day_name}, {d.strftime('%d/%m')} – {time_str}"


# ── Slot generation (cho script tạo JSON mock) ───────────────────────────────

def _booked_slots_mock(day: date, code: str) -> set[int]:
    """Một vài slot coi là đã đặt (deterministic theo ngày + code) để tạo lịch thực tế."""
    seed = day.toordinal() + hash(code) % 97
    all_starts = _all_start_minutes(code)
    if not all_starts:
        return set()
    booked: set[int] = set()
    n = len(all_starts)
    booked.add(all_starts[seed % n])
    if n > 3:
        booked.add(all_starts[(seed + 3) % n])
    return booked


def _all_start_minutes(code: str) -> list[int]:
    prof = CATEGORIES.get(code)
    if not prof:
        return []
    starts: list[int] = []
    dur = prof.duration_minutes
    step = prof.step_minutes
    for block_start, block_end in [
        (prof.morning_start, prof.morning_end),
        (prof.afternoon_start, prof.afternoon_end),
    ]:
        t = _hm_to_minutes(block_start)
        end = _hm_to_minutes(block_end)
        while t + dur <= end:
            starts.append(t)
            t += step
    return starts


def valid_start_minutes_for_category(day: date, code: Optional[str]) -> list[int]:
    """Mốc bắt đầu (phút) hợp lệ, chưa bị booked mock."""
    if day.weekday() >= 5:
        return []
    c = normalize_category_code(code)
    booked = _booked_slots_mock(day, c)
    return [s for s in _all_start_minutes(c) if s not in booked]


def build_slot_dict(base_date: datetime, start_m: int, code: Optional[str]) -> dict:
    h, rem = divmod(start_m, 60)
    slot_dt = base_date.replace(hour=h, minute=rem, second=0, microsecond=0)
    ts = minutes_to_hm(start_m)
    prof = category_profile(code)
    d = base_date.date()
    disp = _slot_display_label(d, ts) + f" ({prof.duration_minutes}p · {prof.label_vi})"
    return {
        "datetime_str": slot_dt.isoformat(),
        "display": disp,
        "time_hm": ts,
        "duration_minutes": prof.duration_minutes,
        "category_code": prof.code,
    }


def slots_for_date_and_category(target: datetime, code: Optional[str], limit: int = 12) -> list[dict]:
    day = target.date()
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    starts = valid_start_minutes_for_category(day, code)
    return [build_slot_dict(base, sm, code) for sm in starts[:limit]]
