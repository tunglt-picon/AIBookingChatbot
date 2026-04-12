"""
Mock Schedule Service – interacts with the `reservations` table.

In production, this would call an actual clinic scheduling system (HIS/EHR API).
The mock generates realistic time slots and writes reservations to PostgreSQL.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def resolve_requested_slot(
    date_iso: str,
    hour: int,
    minute: int,
    dental_case_code: Optional[str] = None,
) -> dict:
    """
    Kiểm tra lịch theo **loại lý do khám** (thời lượng + khung giờ riêng).
    """
    from app.domain.dental_cases import (
        build_slot_dict,
        minutes_to_hm,
        normalize_case_code,
        valid_start_minutes_for_case,
    )

    code = normalize_case_code(dental_case_code)
    try:
        raw = date_iso.strip()
        day = date.fromisoformat(raw) if len(raw) <= 10 else datetime.fromisoformat(raw).date()
    except ValueError:
        return {
            "kind": "closed",
            "slot": None,
            "alternatives": [],
            "requested_label": f"{hour:02d}:{minute:02d}",
        }

    if day.weekday() >= 5:
        return {
            "kind": "closed",
            "slot": None,
            "alternatives": [],
            "requested_label": f"{hour:02d}:{minute:02d}",
        }

    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    starts = valid_start_minutes_for_case(day, code)
    if not starts:
        return {
            "kind": "closed",
            "slot": None,
            "alternatives": [],
            "requested_label": f"{hour:02d}:{minute:02d}",
        }

    req_mins = hour * 60 + minute
    snapped = int(round(req_mins / 15) * 15)
    snapped = max(0, min(snapped, 24 * 60 - 1))

    if snapped in starts:
        slot = build_slot_dict(base, snapped, code)
        return {
            "kind": "exact_available",
            "slot": slot,
            "alternatives": [],
            "requested_label": slot.get("time_hm", minutes_to_hm(snapped)),
        }

    nearest = min(starts, key=lambda s: abs(s - req_mins))
    if abs(nearest - req_mins) <= 22:
        slot = build_slot_dict(base, nearest, code)
        return {
            "kind": "exact_available",
            "slot": slot,
            "alternatives": [],
            "requested_label": slot.get("time_hm", minutes_to_hm(nearest)),
        }

    alts_sorted = sorted(starts, key=lambda s: abs(s - req_mins))[:6]
    alts = [build_slot_dict(base, s, code) for s in alts_sorted]
    return {
        "kind": "suggest",
        "slot": None,
        "alternatives": alts,
        "requested_label": minutes_to_hm(snapped),
    }


# Vietnamese weekday keywords → Python weekday (Mon=0 … Sun=6); used for user message parsing
_VI_WEEKDAY_PHRASES: tuple[tuple[str, int], ...] = (
    ("chủ nhật", 6),
    ("cn", 6),
    ("thứ hai", 0),
    ("thứ 2", 0),
    ("thứ ba", 1),
    ("thứ 3", 1),
    ("thứ tư", 2),
    ("thứ 4", 2),
    ("thứ năm", 3),
    ("thứ 5", 3),
    ("thứ sáu", 4),
    ("thứ 6", 4),
    ("thứ bảy", 5),
    ("thứ 7", 5),
)


def infer_date_str_from_user_text(user_text: str) -> Optional[str]:
    """
    If the patient mentions a Vietnamese weekday or 'ngày mai', return YYYY-MM-DD
    for mock slot generation. Otherwise None (caller uses default next working day).
    """
    if not user_text:
        return None
    low = user_text.lower()
    for phrase, target_wd in _VI_WEEKDAY_PHRASES:
        if phrase in low:
            today = datetime.now(timezone.utc).date()
            delta = target_wd - today.weekday()
            if delta < 0:
                delta += 7
            d = today + timedelta(days=delta)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            return d.isoformat()
    if "ngày mai" in low:
        d = datetime.now(timezone.utc).date() + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d.isoformat()
    return None


@tool
async def get_mock_schedule(
    scope: str = "day",
    date_str: Optional[str] = None,
    dental_case_code: Optional[str] = None,
    week_start_iso: Optional[str] = None,
) -> str:
    """
    Đọc lịch trống **chỉ từ file mock** `lich_trong_tuan_trong_vi.json` (không gọi thuật toán sinh slot).

    Args:
        scope: "day" — một ngày; "week" — cả tuần trong file.
        date_str: YYYY-MM-DD khi scope=day. Bỏ trống → chọn ngày đầu tiên trong file có slot cho dental_case_code.
        dental_case_code: CAVITY | IMPLANT | GINGIVITIS | SCALING | EMERGENCY (mặc định SCALING).
        week_start_iso: Khi scope=week, phải khớp meta.tuan_bat_dau_iso nếu truyền; bỏ trống = tuần mặc định file.

    Returns:
        scope=day: JSON { scope, ok, date, dental_case_code, slots, nguon_du_lieu, loi? }.
        scope=week: JSON { scope, ok, meta, ngay, nguon_du_lieu, loi? } (cấu trúc như build_week_availability_payload).
    """
    from app.domain.dental_cases import normalize_case_code

    from app.services.mock_week_schedule_loader import (
        build_week_availability_payload,
        first_mock_date_iso_for_case,
        get_mock_slots_for_date_and_case,
        list_mock_date_isos,
    )

    code = normalize_case_code(dental_case_code)
    src = "lich_trong_tuan_trong_vi.json"
    low = (scope or "day").strip().lower()

    if low in ("week", "tuan", "whole_week", "ca_tuan"):
        payload = build_week_availability_payload(
            dental_case_code=dental_case_code,
            week_start_iso=week_start_iso,
        )
        payload["scope"] = "week"
        payload["nguon_du_lieu"] = src
        logger.info(
            "[tool] get_mock_schedule week case=%s week=%s ok=%s",
            dental_case_code,
            week_start_iso,
            payload.get("ok"),
        )
        return json.dumps(payload, ensure_ascii=False)

    mock_days = set(list_mock_date_isos())
    if date_str and date_str.strip():
        d_iso = date_str.strip()[:10]
    else:
        d_iso = first_mock_date_iso_for_case(code)

    if d_iso not in mock_days:
        return json.dumps(
            {
                "scope": "day",
                "ok": False,
                "date": d_iso,
                "dental_case_code": code,
                "slots": [],
                "nguon_du_lieu": src,
                "loi": f"Ngày {d_iso} không có trong file mock. Các ngày có dữ liệu: {sorted(mock_days)}",
            },
            ensure_ascii=False,
        )

    slots = get_mock_slots_for_date_and_case(d_iso, code, limit=12)
    payload = {
        "scope": "day",
        "ok": True,
        "date": d_iso,
        "dental_case_code": code,
        "slots": slots,
        "nguon_du_lieu": src,
    }
    logger.info("[tool] get_mock_schedule day date=%s case=%s count=%s", d_iso, code, len(slots))
    return json.dumps(payload, ensure_ascii=False)


@tool
async def book_appointment(
    patient_user_id: int,
    intake_id: int,
    datetime_str: str,
) -> str:
    """
    Book an appointment slot and persist a Reservation record to the database.

    Args:
        patient_user_id: The ID of the patient.
        intake_id: The ID of the BookingConsultIntake record.
        datetime_str: ISO datetime string for the appointment.

    Returns:
        JSON string with the created reservation details.
    """
    from app.database import async_session_factory
    from app.models.reservation import Reservation, ReservationSource, ReservationStatus

    try:
        visit_dt = datetime.fromisoformat(datetime_str)
    except ValueError:
        logger.warning("[tool] book_appointment invalid datetime_str=%r", datetime_str)
        return json.dumps({"error": f"Invalid datetime format: {datetime_str}"})

    logger.info(
        "[tool] book_appointment patient_user_id=%s intake_id=%s at=%s",
        patient_user_id,
        intake_id,
        visit_dt.isoformat(),
    )
    async with async_session_factory() as session:
        reservation = Reservation(
            patient_user_id=patient_user_id,
            booking_consult_intake_id=intake_id,
            schedule_visit_datetime=visit_dt,
            source=ReservationSource.BOOKING_AI,
            status=ReservationStatus.CONFIRMED,
        )
        session.add(reservation)
        await session.commit()
        await session.refresh(reservation)

        return json.dumps({
            "reservation_id": reservation.id,
            "datetime_str": visit_dt.isoformat(),
            "display": visit_dt.strftime("%H:%M, %d/%m/%Y"),
            "status": reservation.status.value,
        }, ensure_ascii=False)
