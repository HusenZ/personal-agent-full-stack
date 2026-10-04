import uuid
from typing import Any

from app.application.onboarding.dto import OnboardingSessionState
from app.domain.analysis.evidence import RawEvidence
from app.infrastructure.database.repositories.onboarding import OnboardingSessionRepository
from app.infrastructure.database.repositories.profile import ProfileRepository


async def collect_profile_evidence(
    user_id: uuid.UUID,
    profiles: ProfileRepository,
    onboarding_sessions: OnboardingSessionRepository,
) -> RawEvidence:
    """Everything the structured profile and the interview say about a user.

    Shared by analysis and planning so both cite the same evidence, with the
    same ids, for the same user.
    """
    profile = await profiles.get_by_user_id(user_id)

    return RawEvidence(
        goals=tuple(goal.name for goal in profile.goals) if profile else (),
        constraints=tuple(item.description for item in profile.constraints) if profile else (),
        preferences=(
            tuple((item.key, item.value) for item in profile.preferences) if profile else ()
        ),
        observations=await _onboarding_observations(user_id, onboarding_sessions),
    )


async def _onboarding_observations(
    user_id: uuid.UUID, onboarding_sessions: OnboardingSessionRepository
) -> tuple[str, ...]:
    """Read what onboarding learned that the profile has no column for.

    Observations describe the user's situation rather than their goals or
    limits, so the profile tables have nowhere to put them. Reading them from
    the interview keeps them available as evidence until semantic memory gives
    them a home of their own.
    """
    onboarding = await onboarding_sessions.get_latest_for_user(user_id)
    if onboarding is None:
        return ()

    state = OnboardingSessionState.model_validate(onboarding.state)
    return tuple(observation.content for observation in state.collected.observations)


def serialise_evidence(raw: RawEvidence) -> dict[str, object]:
    return {
        "goals": list(raw.goals),
        "constraints": list(raw.constraints),
        "preferences": [list(pair) for pair in raw.preferences],
        "observations": list(raw.observations),
    }


def deserialise_evidence(snapshot: dict[str, Any]) -> RawEvidence:
    return RawEvidence(
        goals=tuple(snapshot.get("goals") or ()),
        constraints=tuple(snapshot.get("constraints") or ()),
        preferences=tuple((key, value) for key, value in snapshot.get("preferences") or ()),
        observations=tuple(snapshot.get("observations") or ()),
    )
