from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    patient_user_id: int
    username: str
    full_name: str | None = None


class CurrentUser(BaseModel):
    id: int
    username: str
    patient_no: str

    model_config = {"from_attributes": True}
