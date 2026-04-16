from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.reservation import ReservationSource, ReservationStatus

__all__ = ["AvailableSlot", "SlotsResponse", "ReservationResponse"]


class AvailableSlot(BaseModel):
    datetime_str: str
    display: str
    time_hm: Optional[str] = None
    duration_minutes: Optional[int] = None
    category_code: Optional[str] = None


class SlotsResponse(BaseModel):
    date: str
    category_code: Optional[str] = None
    slots: list[AvailableSlot]


class ReservationResponse(BaseModel):
    id: int
    patient_user_id: int
    booking_consult_intake_id: int
    schedule_visit_datetime: datetime
    source: ReservationSource
    status: ReservationStatus
    created_at: datetime

    model_config = {"from_attributes": True}
