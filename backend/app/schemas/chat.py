from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.session import SenderType, SessionStatus


class SessionCreate(BaseModel):
    pass  # session is implicitly created per-patient when needed


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


# SSE event types streamed back to the client
class SSETokenEvent(BaseModel):
    type: str = "token"
    content: str


class SSEStatusEvent(BaseModel):
    type: str = "status"
    message: str


class SSEDoneEvent(BaseModel):
    type: str = "done"
    session_id: int
    agent: str  # "root" | "specialist"
    booking: Optional[dict] = None
    intake: Optional[dict] = None
