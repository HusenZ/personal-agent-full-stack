import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from app.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

SECRET = SecretStr("a" * 48)
OTHER_SECRET = SecretStr("b" * 48)
ALGORITHM = "HS256"


def test_password_hash_is_not_the_password():
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$argon2")


def test_password_hashes_are_salted():
    assert hash_password("same-password") != hash_password("same-password")


def test_correct_password_verifies():
    assert verify_password("s3cret-passphrase", hash_password("s3cret-passphrase")) is True


def test_wrong_password_is_rejected():
    assert verify_password("wrong-passphrase", hash_password("s3cret-passphrase")) is False


def test_missing_hash_is_rejected_without_raising():
    """Login passes None for an unknown email; it must return False, not blow up."""
    assert verify_password("any-password", None) is False


def test_malformed_hash_is_rejected():
    assert verify_password("any-password", "not-an-argon2-hash") is False


def test_access_token_round_trips():
    user_id = uuid.uuid4()

    token, expires_at = create_access_token(user_id, SECRET, ALGORITHM, ttl_seconds=900)
    claims = decode_access_token(token, SECRET, ALGORITHM)

    assert claims.user_id == user_id
    assert claims.expires_at == expires_at.replace(microsecond=0)


def test_token_signed_with_another_secret_is_rejected():
    token, _ = create_access_token(uuid.uuid4(), OTHER_SECRET, ALGORITHM, ttl_seconds=900)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, SECRET, ALGORITHM)


def test_expired_token_is_rejected():
    token, _ = create_access_token(uuid.uuid4(), SECRET, ALGORITHM, ttl_seconds=-1)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, SECRET, ALGORITHM)


def test_unsigned_token_is_rejected():
    """A 'none' algorithm token must never be accepted, however well-formed."""
    forged = jwt.encode({"sub": str(uuid.uuid4())}, key="", algorithm="none")

    with pytest.raises(InvalidTokenError):
        decode_access_token(forged, SECRET, ALGORITHM)


def test_token_of_another_type_is_rejected():
    expires_at = datetime.now(tz=UTC) + timedelta(minutes=5)
    other = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iat": int(datetime.now(tz=UTC).timestamp()),
            "exp": int(expires_at.timestamp()),
            "typ": "refresh",
        },
        SECRET.get_secret_value(),
        algorithm=ALGORITHM,
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(other, SECRET, ALGORITHM)


def test_token_without_required_claims_is_rejected():
    token = jwt.encode({"sub": str(uuid.uuid4())}, SECRET.get_secret_value(), algorithm=ALGORITHM)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, SECRET, ALGORITHM)


def test_token_subject_must_be_a_user_id():
    expires_at = datetime.now(tz=UTC) + timedelta(minutes=5)
    token = jwt.encode(
        {
            "sub": "not-a-uuid",
            "iat": int(datetime.now(tz=UTC).timestamp()),
            "exp": int(expires_at.timestamp()),
            "typ": "access",
        },
        SECRET.get_secret_value(),
        algorithm=ALGORITHM,
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(token, SECRET, ALGORITHM)


def test_refresh_tokens_are_unique_and_long():
    tokens = {generate_refresh_token() for _ in range(100)}

    assert len(tokens) == 100
    assert all(len(token) >= 40 for token in tokens)


def test_refresh_token_hash_is_deterministic_and_hides_the_token():
    token = generate_refresh_token()

    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert token not in hash_refresh_token(token)
    assert len(hash_refresh_token(token)) == 64


class TestPasswordHashingOffTheEventLoop:
    async def test_hashing_and_verifying_run_outside_the_event_loop_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Inline, each ~50 ms hash froze every other request, streams included."""
        import threading

        from app.core import security

        threads: list[int] = []
        original = security.verify_password

        def recording(password: str, password_hash: str | None) -> bool:
            threads.append(threading.get_ident())
            return original(password, password_hash)

        monkeypatch.setattr(security, "verify_password", recording)
        stored = await security.hash_password_off_loop("a-sufficiently-long-password")

        assert await security.verify_password_off_loop("a-sufficiently-long-password", stored)
        assert threads and threading.get_ident() not in threads
