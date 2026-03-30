import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.patient import PatientUser
    from app.models.reservation import BookingConsultIntake


class SessionStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    INCOMPLETE = "INCOMPLETE"


class SenderType(str, enum.Enum):
    PATIENT_USER = "PATIENT_USER"
    ROOT_AGENT = "ROOT_AGENT"
    SPECIALIST_AGENT = "SPECIALIST_AGENT"
    SYSTEM = "SYSTEM"


class BookingChatSession(Base):
    __tablename__ = "booking_chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patient_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), default=SessionStatus.PROCESSING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    patient_user: Mapped["PatientUser"] = relationship("PatientUser", back_populates="sessions")
    messages: Mapped[List["BookingChatMessage"]] = relationship(
        "BookingChatMessage",
        back_populates="session",
        order_by="BookingChatMessage.created_at",
        cascade="all, delete-orphan",
    )
    intakes: Mapped[List["BookingConsultIntake"]] = relationship(
        "BookingConsultIntake", back_populates="session"
    )

    def __repr__(self) -> str:
        return f"<BookingChatSession id={self.id} status={self.status}>"


class BookingChatMessage(Base):
    __tablename__ = "booking_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("booking_chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_type: Mapped[SenderType] = mapped_column(Enum(SenderType), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    session: Mapped["BookingChatSession"] = relationship("BookingChatSession", back_populates="messages")

    def __repr__(self) -> str:
        return f"<BookingChatMessage id={self.id} sender={self.sender_type}>"
