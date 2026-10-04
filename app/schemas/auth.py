import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# Min length resists guessing; max stops CPU abuse via Argon2.
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class RegisteredUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    # The OAuth 2.0 token_type label, not a credential.
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime
