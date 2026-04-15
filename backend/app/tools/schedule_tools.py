"""
Schedule tools cho LangGraph agent.

- resolve_requested_slot: parse thời gian BN yêu cầu → slot cụ thể.
- get_mock_schedule: đọc lịch trống mock (JSON) theo category + ngày.
- book_appointment: ghi Reservation vào PostgreSQL.

Production: thay mock bằng HIS/EHR API thật.
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
    category_code: Optional[str] = None,
) -> dict:
    """
    Kiểm tra lịch theo category (thời lượng + khung giờ riêng).
    """
    from app.domain.dental_cases import (
        build_slot_dict,
        minutes_to_hm,
        normalize_category_code,
        valid_start_minutes_for_category,
    )

    code = normalize_category_code(category_code)
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
    starts = valid_start_minutes_for_category(day, code)
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


# Vietnamese weekday keywords → Python weekday
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


def infer_date_strs_from_user_text(user_text: str) -> list[str]:
    if not user_text:
        return []
    low = user_text.lower()
    today = datetime.now(timezone.utc).date()
    found: list[str] = []
    seen: set[str] = set()

    for phrase, target_wd in _VI_WEEKDAY_PHRASES:
        if phrase in low:
            delta = target_wd - today.weekday()
            if delta < 0:
                delta += 7
            d = today + timedelta(days=delta)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            iso = d.isoformat()
            if iso not in seen:
                seen.add(iso)
                found.append(iso)
    if "ngày mai" in low:
        d = today + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        iso = d.isoformat()
        if iso not in seen:
            seen.add(iso)
            found.append(iso)
    return found


def infer_date_str_from_user_text(user_text: str) -> Optional[str]:
    dates = infer_date_strs_from_user_text(user_text)
    return dates[0] if dates else None


@tool
async def get_mock_schedule(
    scope: str = "day",
    date_str: Optional[str] = None,
    category_code: Optional[str] = None,
    week_start_iso: Optional[str] = None,
) -> str:
    """
    Đọc lịch trống từ file mock lich_trong_tuan_trong_vi.json.

    Args:
        scope: "day" hoặc "week".
        date_str: YYYY-MM-DD khi scope=day.
        category_code: CAT-01 | CAT-02 | CAT-03 | CAT-04 | CAT-05.
        week_start_iso: Khi scope=week.

    Returns:
        JSON string.
    """
    from app.domain.dental_cases import normalize_category_code
    from app.services.mock_week_schedule_loader import (
        build_week_availability_payload,
        first_mock_date_iso_for_category,
        get_mock_slots_for_date_and_category,
        list_mock_date_isos,
    )

    code = normalize_category_code(category_code)
    src = "lich_trong_tuan_trong_vi.json"
    low = (scope or "day").strip().lower()

    if low in ("week", "tuan", "whole_week", "ca_tuan"):
        payload = build_week_availability_payload(
            category_code=category_code,
            week_start_iso=week_start_iso,
        )
        payload["scope"] = "week"
        payload["nguon_du_lieu"] = src
        logger.info("[tool] get_mock_schedule week category=%s ok=%s", category_code, payload.get("ok"))
        return json.dumps(payload, ensure_ascii=False)

    mock_days = set(list_mock_date_isos())
    if date_str and date_str.strip():
        d_iso = date_str.strip()[:10]
    else:
        d_iso = first_mock_date_iso_for_category(code)

    if d_iso not in mock_days:
        return json.dumps({
            "scope": "day",
            "ok": False,
            "date": d_iso,
            "category_code": code,
            "slots": [],
            "nguon_du_lieu": src,
            "loi": f"Ngày {d_iso} không có trong file mock. Các ngày: {sorted(mock_days)}",
        }, ensure_ascii=False)

    slots = get_mock_slots_for_date_and_category(d_iso, code, limit=12)
    payload = {
        "scope": "day",
        "ok": True,
        "date": d_iso,
        "category_code": code,
        "slots": slots,
        "nguon_du_lieu": src,
    }
    logger.info("[tool] get_mock_schedule day date=%s category=%s count=%s", d_iso, code, len(slots))
    return json.dumps(payload, ensure_ascii=False)


@tool
async def book_appointment(
    patient_user_id: int,
    intake_id: int,
    datetime_str: str,
) -> str:
    """
    Book an appointment slot and persist a Reservation record.
    """
    from app.database import async_session_factory
    from app.models.reservation import Reservation, ReservationSource, ReservationStatus

    try:
        visit_dt = datetime.fromisoformat(datetime_str)
    except ValueError:
        logger.warning("[tool] book_appointment invalid datetime_str=%r", datetime_str)
        return json.dumps({"error": f"Invalid datetime format: {datetime_str}"})

    logger.info(
        "[tool] book_appointment patient=%s intake=%s at=%s",
        patient_user_id, intake_id, visit_dt.isoformat(),
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
