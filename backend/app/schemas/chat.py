from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.session import SenderType, SessionStatus


class SessionResponse(BaseModel):
    id: int
    patient_user_id: int
    status: SessionStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: int
    session_id: int
    sender_type: SenderType
    content: str
    image_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionWithMessages(SessionResponse):
    messages: list[MessageResponse] = []
