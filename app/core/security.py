import asyncio
import hashlib
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import SecretStr

_password_hasher = PasswordHasher()

# Dummy hash so unknown emails cost the same time as real ones.
_DUMMY_PASSWORD_HASH = _password_hasher.hash("timing-equalisation-placeholder")

TokenType = Literal["access"]


class InvalidTokenError(Exception):
    """Raised when an access token is absent, malformed, expired or not ours."""


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    expires_at: datetime


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password, spending comparable time when the account is unknown."""
    if password_hash is None:
        try:
            _password_hasher.verify(_DUMMY_PASSWORD_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        return False

    try:
        _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False

    return True


# Argon2 is slow and memory-hungry; a small pool keeps it off the event loop.
_HASHING_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="password-hashing")


async def hash_password_off_loop(password: str) -> str:
    return await asyncio.get_running_loop().run_in_executor(_HASHING_POOL, hash_password, password)


async def verify_password_off_loop(password: str, password_hash: str | None) -> bool:
    return await asyncio.get_running_loop().run_in_executor(
        _HASHING_POOL, verify_password, password, password_hash
    )


def password_needs_rehash(password_hash: str) -> bool:
    """Report whether a stored hash uses outdated Argon2 parameters."""
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_access_token(
    user_id: uuid.UUID, secret: SecretStr, algorithm: str, ttl_seconds: int
) -> tuple[str, datetime]:
    issued_at = datetime.now(tz=UTC)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)

    claims: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
        # Type claim stops other token kinds being replayed as access tokens.
        "typ": "access",
    }

    return jwt.encode(claims, secret.get_secret_value(), algorithm=algorithm), expires_at


def decode_access_token(token: str, secret: SecretStr, algorithm: str) -> AccessTokenClaims:
    try:
        claims = jwt.decode(
            token,
            secret.get_secret_value(),
            algorithms=[algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise InvalidTokenError(str(error)) from error

    if claims.get("typ") != "access":
        raise InvalidTokenError("token is not an access token")

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as error:
        raise InvalidTokenError("token subject is not a user id") from error

    return AccessTokenClaims(
        user_id=user_id,
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
    )


def generate_refresh_token() -> str:
    """Produce an opaque refresh token.

    Opaque rather than a JWT so that presenting it requires a database lookup,
    which is what makes revocation and reuse detection possible.
    """
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage and lookup.

    SHA-256 rather than Argon2: the token is 256 bits of system randomness, so
    there is nothing to brute-force, and lookup by hash must stay a single
    indexed query rather than a scan over per-row salts.
    """
    return hashlib.sha256(token.encode()).hexdigest()
