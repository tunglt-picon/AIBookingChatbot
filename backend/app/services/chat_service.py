from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.session import BookingChatSession, BookingChatMessage, SenderType, SessionStatus


async def create_session(db: AsyncSession, patient_user_id: int) -> BookingChatSession:
    session = BookingChatSession(
        patient_user_id=patient_user_id,
        status=SessionStatus.PROCESSING,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(
    db: AsyncSession, session_id: int, patient_user_id: Optional[int] = None
) -> Optional[BookingChatSession]:
    query = select(BookingChatSession).where(BookingChatSession.id == session_id)
    if patient_user_id is not None:
        query = query.where(BookingChatSession.patient_user_id == patient_user_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_session_with_messages(
    db: AsyncSession, session_id: int
) -> Optional[BookingChatSession]:
    result = await db.execute(
        select(BookingChatSession)
        .options(selectinload(BookingChatSession.messages))
        .where(BookingChatSession.id == session_id)
    )
    return result.scalar_one_or_none()


async def save_message(
    db: AsyncSession,
    session_id: int,
    sender_type: SenderType,
    content: str,
    image_url: Optional[str] = None,
) -> BookingChatMessage:
    msg = BookingChatMessage(
        session_id=session_id,
        sender_type=sender_type,
        content=content,
        image_url=image_url,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def update_session_status(
    db: AsyncSession, session_id: int, status: SessionStatus
) -> None:
    result = await db.execute(
        select(BookingChatSession).where(BookingChatSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session:
        session.status = status
        await db.commit()


async def list_sessions(
    db: AsyncSession, patient_user_id: int
) -> list[BookingChatSession]:
    result = await db.execute(
        select(BookingChatSession)
        .where(BookingChatSession.patient_user_id == patient_user_id)
        .order_by(BookingChatSession.created_at.desc())
    )
    return list(result.scalars().all())
