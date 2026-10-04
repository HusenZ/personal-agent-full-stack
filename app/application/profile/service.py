import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.profile.dto import UpdateProfileCommand
from app.application.profile.exceptions import ProfileNotFoundError
from app.infrastructure.database.models import Constraint, Goal, Preference, Profile
from app.infrastructure.database.repositories.profile import ProfileRepository

logger = logging.getLogger(__name__)


class ProfileService:
    def __init__(self, session: AsyncSession, profiles: ProfileRepository) -> None:
        self._session = session
        self._profiles = profiles

    async def get_for_user(self, user_id: uuid.UUID) -> Profile:
        profile = await self._profiles.get_by_user_id(user_id)
        if profile is None:
            raise ProfileNotFoundError()
        return profile

    async def update_for_user(self, user_id: uuid.UUID, command: UpdateProfileCommand) -> Profile:
        """Apply a partial update, replacing each supplied collection.

        Replacement rather than merging keeps the semantics predictable: there is
        no stable client-visible identity for a goal or constraint yet, so item
        level patching would have to guess which entries correspond.
        """
        await self.apply_update(user_id, command)
        await self._session.commit()

        logger.info("profile_updated", extra={"user_id": str(user_id)})

        return await self.get_for_user(user_id)

    async def apply_update(self, user_id: uuid.UUID, command: UpdateProfileCommand) -> Profile:
        """Stage a profile update without committing.

        Exists so that a caller which already owns a transaction — onboarding
        completing, for instance — can write the profile and its own state
        atomically instead of committing twice and risking a half-applied result.
        """
        profile = await self.get_for_user(user_id)

        if command.timezone is not None:
            profile.timezone = command.timezone

        if command.goals is not None:
            profile.goals = [
                Goal(name=goal.name, priority=goal.priority, position=position)
                for position, goal in enumerate(command.goals)
            ]

        if command.constraints is not None:
            profile.constraints = [
                Constraint(description=description, position=position)
                for position, description in enumerate(command.constraints)
            ]

        if command.preferences is not None:
            profile.preferences = [
                Preference(key=key, value=value)
                for key, value in sorted(command.preferences.items())
            ]

        return profile
