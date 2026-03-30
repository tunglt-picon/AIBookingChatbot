from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.reservation import ReservationSource, ReservationStatus


class AvailableSlot(BaseModel):
    datetime_str: str  # ISO format
    display: str       # human-readable label (locale-specific), e.g. "Mon, 24/03 – 14:00"


class SlotsResponse(BaseModel):
    date: str
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


class ConsultIntakeResponse(BaseModel):
    id: int
    session_id: int
    symptoms: Optional[str] = None
    ai_diagnosis: Optional[str] = None
    needs_visit: bool
    created_at: datetime

    model_config = {"from_attributes": True}
