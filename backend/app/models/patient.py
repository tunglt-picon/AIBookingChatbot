from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.session import BookingChatSession
    from app.models.reservation import BookingConsultIntake, Reservation


class PatientUser(Base):
    __tablename__ = "patient_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    profile: Mapped[Optional["PatientProfile"]] = relationship(
        "PatientProfile", back_populates="patient_user", uselist=False, cascade="all, delete-orphan"
    )
    sessions: Mapped[List["BookingChatSession"]] = relationship(
        "BookingChatSession", back_populates="patient_user", cascade="all, delete-orphan"
    )
    intakes: Mapped[List["BookingConsultIntake"]] = relationship(
        "BookingConsultIntake", back_populates="patient_user"
    )
    reservations: Mapped[List["Reservation"]] = relationship(
        "Reservation", back_populates="patient_user"
    )

    def __repr__(self) -> str:
        return f"<PatientUser id={self.id} username={self.username!r}>"


class PatientProfile(Base):
    __tablename__ = "patient_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patient_users.id", ondelete="CASCADE"), unique=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    address: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    patient_user: Mapped["PatientUser"] = relationship("PatientUser", back_populates="profile")

    def __repr__(self) -> str:
        return f"<PatientProfile id={self.id} full_name={self.full_name!r}>"
