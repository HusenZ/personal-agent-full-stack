import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RegisterUserCommand:
    email: str
    password: str


@dataclass(frozen=True)
class LoginCommand:
    email: str
    password: str


@dataclass(frozen=True)
class RegisteredUser:
    id: uuid.UUID
    email: str
    created_at: datetime


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime
