from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.reservation import Reservation
from app.schemas.reservation import ReservationResponse, SlotsResponse
from app.services import auth_service
from app.tools.schedule_tools import _generate_mock_slots

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
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return mock available appointment slots for a given date."""
    await _get_current_user(authorization, db)

    if date:
        try:
            target = datetime.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    else:
        target = datetime.now(timezone.utc) + timedelta(days=1)

    while target.weekday() >= 5:
        target += timedelta(days=1)

    raw_slots = _generate_mock_slots(target)
    from app.schemas.reservation import AvailableSlot

    slots = [AvailableSlot(**s) for s in raw_slots[:12]]
    return SlotsResponse(date=target.strftime("%Y-%m-%d"), slots=slots)


@router.get("/reservations", response_model=list[ReservationResponse])
async def list_reservations(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return all reservations for the authenticated patient."""
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
