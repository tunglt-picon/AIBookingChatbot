import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.patient import PatientUser
    from app.models.session import BookingChatSession


class ReservationSource(str, enum.Enum):
    BOOKING_AI = "BOOKING_AI"
    MANUAL = "MANUAL"
    PHONE = "PHONE"


class ReservationStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class BookingConsultIntake(Base):
    """Stores consultation intake data collected before booking (symptoms + intake metadata)."""

    __tablename__ = "booking_consult_intakes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patient_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("booking_chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symptoms: Mapped[Optional[str]] = mapped_column(Text)
    ai_diagnosis: Mapped[Optional[str]] = mapped_column(Text)
    needs_visit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    patient_user: Mapped["PatientUser"] = relationship("PatientUser", back_populates="intakes")
    session: Mapped["BookingChatSession"] = relationship("BookingChatSession", back_populates="intakes")
    reservation: Mapped[Optional["Reservation"]] = relationship(
        "Reservation", back_populates="intake", uselist=False
    )

    def __repr__(self) -> str:
        return f"<BookingConsultIntake id={self.id} needs_visit={self.needs_visit}>"


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patient_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    booking_consult_intake_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("booking_consult_intakes.id", ondelete="CASCADE"), nullable=False
    )
    schedule_visit_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[ReservationSource] = mapped_column(
        Enum(ReservationSource), default=ReservationSource.BOOKING_AI, nullable=False
    )
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(ReservationStatus), default=ReservationStatus.CONFIRMED, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    patient_user: Mapped["PatientUser"] = relationship("PatientUser", back_populates="reservations")
    intake: Mapped["BookingConsultIntake"] = relationship("BookingConsultIntake", back_populates="reservation")

    def __repr__(self) -> str:
        return f"<Reservation id={self.id} datetime={self.schedule_visit_datetime}>"
