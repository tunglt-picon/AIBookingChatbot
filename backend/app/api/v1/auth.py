from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.services import auth_service

router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    try:
        user = await auth_service.register_patient(
            db=db,
            username=payload.username,
            password=payload.password,
            full_name=payload.full_name,
            phone=payload.phone,
            address=payload.address,
        )
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists.",
        )

    token = auth_service.create_access_token({"sub": str(user.id)})
    full_name = user.profile.full_name if user.profile else None
    return TokenResponse(
        access_token=token,
        patient_user_id=user.id,
        username=user.username,
        full_name=full_name,
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await auth_service.authenticate_patient(db, payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    token = auth_service.create_access_token({"sub": str(user.id)})
    full_name = user.profile.full_name if user.profile else None
    return TokenResponse(
        access_token=token,
        patient_user_id=user.id,
        username=user.username,
        full_name=full_name,
    )
