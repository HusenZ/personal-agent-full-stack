import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import RefreshToken, RevocationReason


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add(self, token: RefreshToken) -> None:
        self._session.add(token)

    async def revoke_family(
        self, family_id: uuid.UUID, reason: RevocationReason, revoked_at: datetime
    ) -> int:
        """Revoke every still-valid token in a rotation family.

        Issued as one statement rather than loading and mutating rows, because a
        family may be long and the operation runs on a suspected-breach path
        where latency matters.
        """
        statement = (
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at, revocation_reason=reason)
        )
        result = cast(CursorResult[Any], await self._session.execute(statement))
        return result.rowcount
