"""
Quy tắc mock: 5 nhóm lý do khám — mỗi nhóm có thời lượng (phút) và khung giờ được phép
trong ngày làm việc (thứ 2–6). Khác nhau để mô phỏng phòng khám / bác sĩ phù hợp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

# Mã đặt lịch “chung” khi chưa qua phân loại (đặt lịch trực tiếp qua chat).
DEFAULT_CASE_CODE = "SCALING"


@dataclass(frozen=True)
class DentalCaseProfile:
    code: str
    label_vi: str
    duration_minutes: int
    window_start_hm: str  # "HH:MM" — bắt đầu khung cho type
    window_end_hm: str  #: giờ kết thúc muộn nhất của **ca khám** (thời điểm xong hẹn)


# Khung ví dụ cố định (demo). End = thời điểm ca khám phải **kết thúc** trước hoặc bằng mốc này.
DENTAL_CASES: dict[str, DentalCaseProfile] = {
    "CAVITY": DentalCaseProfile(
        "CAVITY",
        "Sâu răng / khắc phục sâu răng",
        15,
        "08:00",
        "08:30",
    ),
    "IMPLANT": DentalCaseProfile(
        "IMPLANT",
        "Trồng răng / implant",
        60,
        "08:00",
        "09:00",
    ),
    "GINGIVITIS": DentalCaseProfile(
        "GINGIVITIS",
        "Viêm nướu / nha chu",
        30,
        "09:30",
        "12:00",
    ),
    "SCALING": DentalCaseProfile(
        "SCALING",
        "Cạo vôi / vệ sinh răng miệng",
        15,
        "13:30",
        "16:30",
    ),
    "EMERGENCY": DentalCaseProfile(
        "EMERGENCY",
        "Đau cấp / sưng nướu / cần xử lý nhanh",
        30,
        "10:00",
        "12:30",
    ),
}


def normalize_case_code(raw: Optional[str]) -> str:
    if raw is None or not isinstance(raw, str):
        return DEFAULT_CASE_CODE
    u = raw.strip().upper()
    if u in DENTAL_CASES:
        return u
    return DEFAULT_CASE_CODE


def case_profile(code: Optional[str]) -> DentalCaseProfile:
    return DENTAL_CASES.get(normalize_case_code(code), DENTAL_CASES[DEFAULT_CASE_CODE])


def case_label_vi(code: Optional[str]) -> str:
    return case_profile(code).label_vi


def _hm_to_minutes(hm: str) -> int:
    h, m = map(int, hm.split(":", 1))
    return h * 60 + m


# Lưới 30p dùng chung với schedule_tools (để mock “đã đặt” nhất quán).
_GRID_TIMES: tuple[str, ...] = (
    "08:00", "08:30", "09:00", "09:30", "10:00", "10:30",
    "11:00", "11:30", "13:30", "14:00", "14:30", "15:00",
    "15:30", "16:00", "16:30",
)


def _slot_display_label(d: date, time_str: str) -> str:
    vi_days = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6")
    day_name = vi_days[d.weekday()]
    return f"{day_name}, {d.strftime('%d/%m')} – {time_str}"


def booked_intervals_mock(day: date) -> list[tuple[int, int]]:
    """Mỗi ngày có hai ô 30p được coi là đã kín (trùng chỉ số lưới cũ)."""
    n = len(_GRID_TIMES)
    indices = {day.day % n, (day.day + 3) % n}
    out: list[tuple[int, int]] = []
    for i in indices:
        ts = _GRID_TIMES[i]
        sm = _hm_to_minutes(ts)
        out.append((sm, sm + 30))
    return out


def _intervals_overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def valid_start_minutes_for_case(day: date, case_code: Optional[str]) -> list[int]:
    """Các mốc bắt đầu (phút từ 00:00) hợp lệ, bước 15p, không chồng lên slot đã đặt mock."""
    if day.weekday() >= 5:
        return []

    prof = case_profile(case_code)
    w0 = _hm_to_minutes(prof.window_start_hm)
    w1 = _hm_to_minutes(prof.window_end_hm)
    dur = prof.duration_minutes
    booked = booked_intervals_mock(day)

    starts: list[int] = []
    t = w0
    step = 15
    while t + dur <= w1:
        ok = True
        for b0, b1 in booked:
            if _intervals_overlap(t, t + dur, b0, b1):
                ok = False
                break
        if ok:
            starts.append(t)
        t += step
    return starts


def minutes_to_hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def build_slot_dict(base: datetime, start_m: int, case_code: Optional[str]) -> dict:
    """Một slot đặt lịch: ISO datetime bắt đầu + display (kèm thời lượng trong display)."""
    h, rem = divmod(start_m, 60)
    slot_dt = base.replace(hour=h, minute=rem, second=0, microsecond=0)
    ts = minutes_to_hm(start_m)
    prof = case_profile(case_code)
    d = base.date()
    disp = _slot_display_label(d, ts) + f" ({prof.duration_minutes}p · {prof.label_vi})"
    return {
        "datetime_str": slot_dt.isoformat(),
        "display": disp,
        "time_hm": ts,
        "duration_minutes": prof.duration_minutes,
        "dental_case_code": prof.code,
    }


def slots_for_date_and_case(target: datetime, case_code: Optional[str], limit: int = 12) -> list[dict]:
    """Danh sách slot available cho ngày + loại bệnh."""
    day = target.date()
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    starts = valid_start_minutes_for_case(day, case_code)
    out: list[dict] = []
    for sm in starts[:limit]:
        out.append(build_slot_dict(base, sm, case_code))
    return out
