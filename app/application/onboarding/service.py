import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.onboarding.dto import OnboardingSessionState, OnboardingTurn
from app.application.onboarding.exceptions import (
    OnboardingAlreadyCompletedError,
    OnboardingNotStartedError,
)
from app.application.profile.dto import GoalInput, UpdateProfileCommand
from app.application.profile.service import ProfileService
from app.graph.onboarding.graph import OnboardingGraph
from app.graph.onboarding.state import AskedQuestion, ProfileDraft
from app.infrastructure.database.models import GoalPriority, OnboardingSession, OnboardingStatus
from app.infrastructure.database.repositories.onboarding import OnboardingSessionRepository

logger = logging.getLogger(__name__)


class OnboardingService:
    """Drives the adaptive interview one HTTP turn at a time.

    The graph advances the interview by a single exchange; this service owns the
    durable state around it, so a turn survives the process that produced it.
    """

    def __init__(
        self,
        session: AsyncSession,
        sessions: OnboardingSessionRepository,
        profiles: ProfileService,
        graph: OnboardingGraph,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._profiles = profiles
        self._graph = graph

    async def start(self, user_id: uuid.UUID) -> OnboardingTurn:
        """Begin the interview, or resume one already in progress.

        Resuming rather than restarting means a client that retries a failed
        request cannot silently discard the answers already collected.
        """
        active = await self._sessions.get_active_for_user(user_id)
        if active is not None:
            return self._to_turn(active, OnboardingSessionState.model_validate(active.state))

        latest = await self._sessions.get_latest_for_user(user_id)
        if latest is not None and latest.status is OnboardingStatus.COMPLETED:
            raise OnboardingAlreadyCompletedError()

        # Set every column explicitly; model defaults only apply at flush.
        onboarding_session = OnboardingSession(
            user_id=user_id,
            status=OnboardingStatus.IN_PROGRESS,
            questions_asked=0,
            overall_confidence=0.0,
            state={},
        )
        self._sessions.add(onboarding_session)

        return await self._advance(
            onboarding_session, OnboardingSessionState(), user_id=user_id, answer=None
        )

    async def submit_answer(self, user_id: uuid.UUID, answer: str) -> OnboardingTurn:
        onboarding_session = await self._sessions.get_active_for_user(user_id)
        if onboarding_session is None:
            raise OnboardingNotStartedError()

        state = OnboardingSessionState.model_validate(onboarding_session.state)

        return await self._advance(onboarding_session, state, user_id=user_id, answer=answer)

    async def get_status(self, user_id: uuid.UUID) -> OnboardingTurn:
        onboarding_session = await self._sessions.get_latest_for_user(user_id)
        if onboarding_session is None:
            raise OnboardingNotStartedError()

        state = OnboardingSessionState.model_validate(onboarding_session.state)
        return self._to_turn(onboarding_session, state)

    async def _advance(
        self,
        onboarding_session: OnboardingSession,
        state: OnboardingSessionState,
        *,
        user_id: uuid.UUID,
        answer: str | None,
    ) -> OnboardingTurn:
        result: dict[str, Any] = await self._graph.ainvoke(
            {
                "user_id": str(user_id),
                "pending_question": state.pending_question,
                "answer": answer,
                "asked_questions": state.asked_questions,
                "questions_asked": onboarding_session.questions_asked,
                "collected": state.collected,
                "errors": [],
            }
        )

        next_question: AskedQuestion | None = result.get("next_question")
        is_complete: bool = bool(result.get("is_complete"))

        updated = OnboardingSessionState(
            collected=result["collected"],
            asked_questions=result.get("asked_questions", []),
            pending_question=next_question,
            coverage=result.get("coverage", {}),
            completion_reason=result.get("completion_reason"),
        )

        onboarding_session.state = updated.model_dump(mode="json")
        onboarding_session.questions_asked = result.get("questions_asked") or 0
        onboarding_session.overall_confidence = result.get("overall_confidence") or 0.0

        if is_complete:
            await self._persist_profile(user_id, result.get("profile_draft"))
            onboarding_session.status = OnboardingStatus.COMPLETED
            onboarding_session.completed_at = datetime.now(tz=UTC)

        # Interview state and resulting profile are committed together.
        await self._session.commit()

        logger.info(
            "onboarding_turn_completed",
            extra={
                "user_id": str(user_id),
                "onboarding_session_id": str(onboarding_session.id),
                "questions_asked": onboarding_session.questions_asked,
                "overall_confidence": round(onboarding_session.overall_confidence, 3),
                "is_complete": is_complete,
                "completion_reason": updated.completion_reason,
            },
        )

        return self._to_turn(onboarding_session, updated)

    async def _persist_profile(self, user_id: uuid.UUID, draft: ProfileDraft | None) -> None:
        if draft is None:
            return

        await self._profiles.apply_update(
            user_id,
            UpdateProfileCommand(
                goals=[
                    GoalInput(name=name, priority=GoalPriority(priority))
                    for name, priority in draft.goals
                ],
                constraints=list(draft.constraints),
                preferences=dict(draft.preferences),
            ),
        )

    @staticmethod
    def _to_turn(
        onboarding_session: OnboardingSession, state: OnboardingSessionState
    ) -> OnboardingTurn:
        is_complete = onboarding_session.status is OnboardingStatus.COMPLETED

        return OnboardingTurn(
            session_id=onboarding_session.id,
            status=onboarding_session.status.value,
            questions_asked=onboarding_session.questions_asked,
            overall_confidence=onboarding_session.overall_confidence,
            coverage=state.coverage,
            question=None if is_complete else state.pending_question,
            is_complete=is_complete,
        )
