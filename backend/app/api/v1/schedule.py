from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.reservation import Reservation
from app.schemas.reservation import AvailableSlot, ReservationResponse, SlotsResponse
from app.domain.dental_cases import normalize_category_code
from app.services import auth_service
from app.services.mock_week_schedule_loader import (
    build_week_availability_payload,
    first_mock_date_iso_for_category,
    get_mock_slots_for_date_and_category,
    list_mock_date_isos,
)

router = APIRouter()


async def _get_current_user(authorization: Optional[str], db: AsyncSession):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing.")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    try:
        payload = auth_service.decode_token(token)
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token.")
    user = await auth_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    return user


@router.get("/slots", response_model=SlotsResponse)
async def get_available_slots(
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    case: Optional[str] = Query(
        default=None,
        description="CAT-01 | CAT-02 | CAT-03 | CAT-04 | CAT-05",
    ),
):
    """Slot trong một ngày — chỉ từ file mock (dev: không cần JWT)."""
    code = normalize_category_code(case)
    mock_days = set(list_mock_date_isos())

    if date:
        try:
            d_iso = datetime.fromisoformat(date.strip()).date().isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    else:
        d_iso = first_mock_date_iso_for_category(code)

    if d_iso not in mock_days:
        return SlotsResponse(date=d_iso, category_code=code, slots=[])

    raw_slots = get_mock_slots_for_date_and_category(d_iso, code, limit=12)
    slots = [AvailableSlot.model_validate(s) for s in raw_slots]
    return SlotsResponse(
        date=d_iso,
        category_code=code,
        slots=slots,
    )


@router.get("/week/slots")
async def get_week_slots_mock(
    case: Optional[str] = Query(
        default=None,
        description="Lọc một category; bỏ trống = trả đủ loại mỗi ngày",
    ),
    week_start: Optional[str] = Query(
        default=None,
        description="YYYY-MM-DD phải khớp meta.tuan_bat_dau_iso trong file mock",
    ),
):
    """Lịch mock cả tuần (dev: không cần JWT)."""
    return build_week_availability_payload(
        category_code=case,
        week_start_iso=week_start,
    )


@router.get("/reservations", response_model=list[ReservationResponse])
async def list_reservations(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    result = await db.execute(
        select(Reservation)
        .where(Reservation.patient_user_id == user.id)
        .order_by(Reservation.schedule_visit_datetime.desc())
    )
    return list(result.scalars().all())


@router.get("/reservations/{reservation_id}", response_model=ReservationResponse)
async def get_reservation(
    reservation_id: int,
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    result = await db.execute(
        select(Reservation)
        .where(
            Reservation.id == reservation_id,
            Reservation.patient_user_id == user.id,
        )
    )
    reservation = result.scalar_one_or_none()
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    return reservation
