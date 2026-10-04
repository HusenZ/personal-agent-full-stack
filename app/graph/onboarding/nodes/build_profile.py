from app.domain.onboarding.context import CollectedContext
from app.graph.onboarding.state import OnboardingTurnState, ProfileDraft

# Low-confidence evidence isn't written into the profile.
MIN_CONFIDENCE_FOR_PROFILE = 0.4


async def build_profile(state: OnboardingTurnState) -> OnboardingTurnState:
    """Assemble the profile the interview supports.

    Produces a draft only. Writing it is the application layer's transaction.
    """
    collected = state.get("collected") or CollectedContext()

    goals = sorted(
        (goal for goal in collected.goals if goal.confidence >= MIN_CONFIDENCE_FOR_PROFILE),
        key=lambda goal: goal.confidence,
        reverse=True,
    )

    preferences: dict[str, str] = {}
    for preference in sorted(collected.preferences, key=lambda item: item.confidence):
        if preference.confidence >= MIN_CONFIDENCE_FOR_PROFILE:
            # Sorted ascending so the highest-confidence value for a key wins.
            preferences[preference.key] = preference.value

    return {
        # Explicitly None so the completed state says there's no next question.
        "next_question": None,
        "profile_draft": ProfileDraft(
            goals=[(goal.name, goal.priority) for goal in goals],
            constraints=[
                item.description
                for item in collected.constraints
                if item.confidence >= MIN_CONFIDENCE_FOR_PROFILE
            ],
            preferences=preferences,
        ),
    }
