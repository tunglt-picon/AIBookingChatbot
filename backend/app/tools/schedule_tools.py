"""
Mock Schedule Service – interacts with the `reservations` table.

In production, this would call an actual clinic scheduling system (HIS/EHR API).
The mock generates realistic time slots and writes reservations to PostgreSQL.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Clinic grid (30-minute steps, weekdays) — shared by mock slots and availability resolver
SLOT_TIMES: tuple[str, ...] = (
    "08:00", "08:30", "09:00", "09:30", "10:00", "10:30",
    "11:00", "11:30", "13:30", "14:00", "14:30", "15:00",
    "15:30", "16:00", "16:30",
)

_VI_DAY_NAMES: tuple[str, ...] = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6")


def _booked_slot_indices(d: datetime) -> set[int]:
    """Deterministic mock: two slots per day are treated as already taken."""
    n = len(SLOT_TIMES)
    return {d.day % n, (d.day + 3) % n}


def _slot_display_label(d: datetime, time_str: str) -> str:
    day_name = _VI_DAY_NAMES[d.weekday()]
    return f"{day_name}, {d.strftime('%d/%m')} – {time_str}"


def _minutes_since_midnight(time_str: str) -> int:
    h, m = map(int, time_str.split(":"))
    return h * 60 + m


def resolve_requested_slot(date_iso: str, hour: int, minute: int) -> dict:
    """
    Check mock availability for a requested wall time on date_iso (YYYY-MM-DD).

    Returns:
        kind: "exact_available" | "suggest" | "closed"
        slot: dict | None  — chosen slot if exact_available
        alternatives: list[dict] — nearest free slots (for suggest)
        requested_label: str — HH:MM requested (after snapping to grid)
    """
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
    booked = _booked_slot_indices(base)
    req_mins = hour * 60 + minute
    grid_min = _minutes_since_midnight(SLOT_TIMES[0])
    grid_max = _minutes_since_midnight(SLOT_TIMES[-1])
    snapped = round(req_mins / 30) * 30
    snapped = max(grid_min, min(int(snapped), grid_max))

    def build_slot(idx: int) -> dict:
        ts = SLOT_TIMES[idx]
        h, m = map(int, ts.split(":"))
        slot_dt = base.replace(hour=h, minute=m, second=0, microsecond=0)
        return {
            "datetime_str": slot_dt.isoformat(),
            "display": _slot_display_label(base, ts),
        }

    snap_idx = min(
        range(len(SLOT_TIMES)),
        key=lambda i: abs(_minutes_since_midnight(SLOT_TIMES[i]) - snapped),
    )
    requested_label = SLOT_TIMES[snap_idx]

    available_indices = [i for i in range(len(SLOT_TIMES)) if i not in booked]

    if snap_idx not in booked:
        return {
            "kind": "exact_available",
            "slot": build_slot(snap_idx),
            "alternatives": [],
            "requested_label": requested_label,
        }

    # Requested grid slot is "taken" — suggest nearest free slots by time distance
    alts: list[dict] = []
    for i in sorted(
        available_indices,
        key=lambda i: abs(_minutes_since_midnight(SLOT_TIMES[i]) - _minutes_since_midnight(requested_label)),
    ):
        alts.append(build_slot(i))
        if len(alts) >= 4:
            break

    return {
        "kind": "suggest",
        "slot": None,
        "alternatives": alts,
        "requested_label": requested_label,
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


def _generate_mock_slots(date: datetime) -> list[dict]:
    """Generate available 30-min slots for a clinic day (08:00–17:00, weekdays only)."""
    slots = []
    if date.weekday() >= 5:  # skip weekends
        return slots

    booked_indices = _booked_slot_indices(date)
    day_name = _VI_DAY_NAMES[date.weekday()]

    for i, time_str in enumerate(SLOT_TIMES):
        if i not in booked_indices:
            h, m = map(int, time_str.split(":"))
            slot_dt = date.replace(hour=h, minute=m, second=0, microsecond=0)
            slots.append({
                "datetime_str": slot_dt.isoformat(),
                "display": f"{day_name}, {date.strftime('%d/%m')} – {time_str}",
            })
    return slots


@tool
async def get_available_slots(date_str: Optional[str] = None) -> str:
    """
    Get available appointment slots for a given date.

    Args:
        date_str: Date in YYYY-MM-DD format. Defaults to the next working day.

    Returns:
        JSON string with list of available slots, each containing
        'datetime_str' (ISO format) and 'display' (human-readable Vietnamese label).
    """
    if not date_str:
        target = datetime.now(timezone.utc) + timedelta(days=1)
    else:
        try:
            target = datetime.fromisoformat(date_str.strip())
        except ValueError:
            target = datetime.now(timezone.utc) + timedelta(days=1)

    # Advance to next weekday if needed
    while target.weekday() >= 5:
        target += timedelta(days=1)

    slots = _generate_mock_slots(target)
    if not slots:
        # fallback – try next day
        target += timedelta(days=1)
        slots = _generate_mock_slots(target)

    payload = {"date": target.strftime("%Y-%m-%d"), "slots": slots[:12]}
    logger.info("[tool] get_available_slots date=%s count=%s", payload["date"], len(payload["slots"]))
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
