from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.patient import PatientUser, PatientProfile

# Use bcrypt directly – passlib 1.7.4 is incompatible with bcrypt 4+/5+


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


async def register_patient(
    db: AsyncSession,
    username: str,
    password: str,
    full_name: str,
    phone: Optional[str] = None,
    address: Optional[str] = None,
) -> PatientUser:
    from sqlalchemy import func
    from sqlalchemy.orm import selectinload

    count_result = await db.execute(select(func.count()).select_from(PatientUser))
    count = count_result.scalar_one()
    patient_no = f"PT{(count + 1):06d}"

    user = PatientUser(
        username=username,
        password=hash_password(password),
        patient_no=patient_no,
    )
    db.add(user)
    await db.flush()  # populate user.id

    profile = PatientProfile(
        patient_user_id=user.id,
        full_name=full_name,
        phone=phone,
        address=address,
    )
    db.add(profile)
    await db.commit()

    # Reload with eager profile to avoid MissingGreenlet on lazy access
    result = await db.execute(
        select(PatientUser)
        .options(selectinload(PatientUser.profile))
        .where(PatientUser.id == user.id)
    )
    return result.scalar_one()


async def authenticate_patient(
    db: AsyncSession, username: str, password: str
) -> Optional[PatientUser]:
    result = await db.execute(
        select(PatientUser)
        .options(selectinload(PatientUser.profile))
        .where(PatientUser.username == username)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password):
        return None
    return user


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[PatientUser]:
    result = await db.execute(
        select(PatientUser)
        .options(selectinload(PatientUser.profile))
        .where(PatientUser.id == user_id)
    )
    return result.scalar_one_or_none()
