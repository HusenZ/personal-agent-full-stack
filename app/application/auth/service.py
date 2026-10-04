import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth.dto import (
    LoginCommand,
    RegisteredUser,
    RegisterUserCommand,
    TokenPair,
)
from app.application.auth.exceptions import (
    EmailAlreadyRegisteredError,
    InactiveUserError,
    IncorrectPasswordError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.core.config import Settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password_off_loop,
    hash_refresh_token,
    password_needs_rehash,
    verify_password_off_loop,
)
from app.infrastructure.database.models import Profile, RefreshToken, RevocationReason, User
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.infrastructure.database.repositories.refresh_token import RefreshTokenRepository
from app.infrastructure.database.repositories.user import UserRepository, normalise_email

logger = logging.getLogger(__name__)


class AuthService:
    """Registration, login and refresh-token rotation.

    Owns the transaction boundary for these use cases: the session dependency
    does not commit, so each public method commits its own unit of work.
    """

    def __init__(
        self,
        session: AsyncSession,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        profiles: ProfileRepository,
        settings: Settings,
    ) -> None:
        self._session = session
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._profiles = profiles
        self._settings = settings

    async def register(self, command: RegisterUserCommand) -> RegisteredUser:
        email = normalise_email(command.email)

        if await self._users.email_exists(email):
            raise EmailAlreadyRegisteredError()

        user = User(email=email, password_hash=await hash_password_off_loop(command.password))
        self._users.add(user)

        try:
            # Flush to get the user id and surface duplicate emails early.
            await self._session.flush()

            # Create the profile in the same transaction so every user has one.
            self._profiles.add(Profile(user_id=user.id))
            await self._session.commit()
        except IntegrityError as error:
            # The unique index decides concurrent registrations; the loser gets 409.
            await self._session.rollback()
            raise EmailAlreadyRegisteredError() from error

        logger.info("user_registered", extra={"user_id": str(user.id)})

        return RegisteredUser(id=user.id, email=user.email, created_at=user.created_at)

    async def login(self, command: LoginCommand) -> TokenPair:
        user = await self._users.get_by_email(command.email)

        # Always verify, even for unknown emails, so timing doesn't leak accounts.
        password_matches = await verify_password_off_loop(
            command.password, user.password_hash if user else None
        )
        if user is None or not password_matches:
            raise InvalidCredentialsError()

        if not user.is_active:
            raise InactiveUserError()

        if password_needs_rehash(user.password_hash):
            user.password_hash = await hash_password_off_loop(command.password)

        tokens = self._issue_token_pair(user_id=user.id, family_id=uuid.uuid4())
        await self._session.commit()

        logger.info("user_logged_in", extra={"user_id": str(user.id)})

        return tokens

    async def refresh(self, presented_token: str) -> TokenPair:
        token_hash = hash_refresh_token(presented_token)
        stored = await self._refresh_tokens.get_by_hash(token_hash)
        now = datetime.now(tz=UTC)

        if stored is None:
            raise InvalidRefreshTokenError()

        if stored.revoked_at is not None:
            await self._handle_replayed_token(stored, now)
            raise InvalidRefreshTokenError()

        if stored.expires_at <= now:
            raise InvalidRefreshTokenError()

        user = await self._users.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise InvalidRefreshTokenError()

        stored.revoked_at = now
        stored.revocation_reason = RevocationReason.ROTATED

        tokens = self._issue_token_pair(user_id=user.id, family_id=stored.family_id, now=now)
        await self._session.commit()

        return tokens

    async def logout(self, presented_token: str) -> None:
        """Revoke a refresh token and the rest of its family.

        Succeeds silently for an unknown token: logout is not an oracle for
        whether a token was ever valid, and the caller's intent is met either way.
        """
        stored = await self._refresh_tokens.get_by_hash(hash_refresh_token(presented_token))
        if stored is None:
            return

        await self._refresh_tokens.revoke_family(
            stored.family_id, RevocationReason.LOGOUT, datetime.now(tz=UTC)
        )
        await self._session.commit()

        logger.info("user_logged_out", extra={"user_id": str(stored.user_id)})

    async def delete_account(self, user: User, password: str) -> None:
        """Delete the account and everything it owns, after re-checking the password.

        A valid access token alone is not enough: one left on an unlocked phone
        should not be able to erase someone's account. Profile, memories,
        conversations, runs, jobs and refresh tokens all cascade with the user,
        so nothing personal outlives the request.
        """
        if not await verify_password_off_loop(password, user.password_hash):
            raise IncorrectPasswordError()

        user_id = user.id
        await self._users.delete(user_id)
        await self._session.commit()

        logger.info("user_deleted", extra={"user_id": str(user_id)})

    async def _handle_replayed_token(self, stored: RefreshToken, now: datetime) -> None:
        """Revoke the family when an already-rotated token is presented again.

        A rotated token should never be seen twice: either it leaked, or a client
        is replaying. Both mean the family can no longer be trusted, so every
        descendant is revoked and the legitimate holder is forced to log in again.
        """
        if stored.revocation_reason is not RevocationReason.ROTATED:
            return

        revoked = await self._refresh_tokens.revoke_family(
            stored.family_id, RevocationReason.REUSE_DETECTED, now
        )
        await self._session.commit()

        logger.warning(
            "refresh_token_reuse_detected",
            extra={
                "user_id": str(stored.user_id),
                "family_id": str(stored.family_id),
                "revoked_tokens": revoked,
            },
        )

    def _issue_token_pair(
        self, user_id: uuid.UUID, family_id: uuid.UUID, now: datetime | None = None
    ) -> TokenPair:
        issued_at = now or datetime.now(tz=UTC)

        access_token, access_expires_at = create_access_token(
            user_id=user_id,
            secret=self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
            ttl_seconds=self._settings.access_token_ttl_seconds,
        )

        refresh_token = generate_refresh_token()
        refresh_expires_at = issued_at + timedelta(seconds=self._settings.refresh_token_ttl_seconds)

        self._refresh_tokens.add(
            RefreshToken(
                user_id=user_id,
                family_id=family_id,
                token_hash=hash_refresh_token(refresh_token),
                issued_at=issued_at,
                expires_at=refresh_expires_at,
            )
        )

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            access_token_expires_at=access_expires_at,
            refresh_token_expires_at=refresh_expires_at,
        )
