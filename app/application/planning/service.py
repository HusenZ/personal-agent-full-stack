import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.analysis.evidence_source import (
    collect_profile_evidence,
    deserialise_evidence,
    serialise_evidence,
)
from app.application.planning.exceptions import (
    NothingToPlanFromError,
    PlanBlockNotFoundError,
    PlanNotFoundError,
    PlanUnavailableError,
)
from app.domain.analysis.evidence import EvidenceBundle, build_evidence_bundle
from app.domain.planning.week import (
    BlockCategory,
    PlanBlock,
    ProposedWeek,
    check_week,
    plan_dates,
)
from app.domain.temporal.ranges import resolve_timezone
from app.infrastructure.database.models import WeekPlan
from app.infrastructure.database.repositories.onboarding import OnboardingSessionRepository
from app.infrastructure.database.repositories.plan import WeekPlanRepository
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.planning import build_planning_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeekPlanView:
    id: uuid.UUID
    created_at: datetime
    starts_on: date
    timezone: str
    summary: str
    blocks: tuple[PlanBlock, ...]
    evidence: EvidenceBundle


class PlanningService:
    """Plans the user's next seven days from what is known about them.

    One structured model call, then deterministic checks: the model proposes
    blocks, and the domain decides which can go on a calendar. There is no
    retry loop; a week with a few blocks set aside is still a useful week, and
    a failed call is reported rather than replaced with a generic plan.
    """

    def __init__(
        self,
        session: AsyncSession,
        plans: WeekPlanRepository,
        profiles: ProfileRepository,
        onboarding_sessions: OnboardingSessionRepository,
        llm: LLMProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._plans = plans
        self._profiles = profiles
        self._onboarding_sessions = onboarding_sessions
        self._llm = llm
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def generate(self, user_id: uuid.UUID) -> WeekPlanView:
        raw = await collect_profile_evidence(user_id, self._profiles, self._onboarding_sessions)
        evidence = build_evidence_bundle(raw)
        if evidence.is_empty():
            raise NothingToPlanFromError()

        profile = await self._profiles.get_by_user_id(user_id)
        zone = resolve_timezone(profile.timezone if profile else None)
        # "This week" starts today where the user is, not where the server is.
        first_day = self._clock().astimezone(zone).date()
        prompt = build_planning_prompt(evidence, plan_dates(first_day))

        try:
            proposal = await self._llm.generate_structured(prompt, ProposedWeek)
        except LLMError as error:
            logger.warning(
                "week_plan_generation_failed",
                extra={"user_id": str(user_id), "error_type": type(error).__name__},
            )
            raise PlanUnavailableError() from error

        checked = check_week(proposal, first_day, evidence.ids)
        if not checked.blocks:
            logger.warning(
                "week_plan_empty", extra={"user_id": str(user_id), "rejected": checked.rejected}
            )
            raise PlanUnavailableError()

        plan = WeekPlan(
            user_id=user_id,
            starts_on=first_day,
            timezone=zone.key,
            summary=proposal.summary.strip(),
            blocks=[_serialise_block(block) for block in checked.blocks],
            evidence_snapshot=serialise_evidence(raw),
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            rejected_block_count=sum(checked.rejected.values()),
        )
        self._plans.add(plan)
        await self._session.commit()

        logger.info(
            "week_plan_generated",
            extra={
                "user_id": str(user_id),
                "plan_id": str(plan.id),
                "block_count": len(checked.blocks),
                "rejected": checked.rejected,
            },
        )
        return _view(plan, evidence)

    async def get_latest(self, user_id: uuid.UUID) -> WeekPlanView:
        plan = await self._plans.get_latest_for_user(user_id)
        if plan is None:
            raise PlanNotFoundError()
        return _view(plan)

    async def set_block_done(
        self, user_id: uuid.UUID, plan_id: uuid.UUID, block_id: str, *, done: bool
    ) -> WeekPlanView:
        plan = await self._plans.get_owned_by(plan_id, user_id, for_update=True)
        if plan is None:
            raise PlanNotFoundError()

        blocks = [_deserialise_block(item) for item in plan.blocks]
        if not any(block.id == block_id for block in blocks):
            raise PlanBlockNotFoundError()

        # Assign a new list; in-place JSONB mutation isn't detected.
        plan.blocks = [
            _serialise_block(replace(block, done=done) if block.id == block_id else block)
            for block in blocks
        ]
        await self._session.commit()
        return _view(plan)


def _view(plan: WeekPlan, evidence: EvidenceBundle | None = None) -> WeekPlanView:
    return WeekPlanView(
        id=plan.id,
        created_at=plan.created_at,
        starts_on=plan.starts_on,
        timezone=plan.timezone,
        summary=plan.summary,
        blocks=tuple(_deserialise_block(item) for item in plan.blocks),
        evidence=evidence or build_evidence_bundle(deserialise_evidence(plan.evidence_snapshot)),
    )


def _serialise_block(block: PlanBlock) -> dict[str, Any]:
    return {
        "id": block.id,
        "on": block.on.isoformat(),
        "start": block.start,
        "duration_minutes": block.duration_minutes,
        "title": block.title,
        "category": block.category.value,
        "reason": block.reason,
        "evidence_ids": list(block.evidence_ids),
        "done": block.done,
    }


def _deserialise_block(item: dict[str, Any]) -> PlanBlock:
    return PlanBlock(
        id=item["id"],
        on=date.fromisoformat(item["on"]),
        start=item.get("start"),
        duration_minutes=item["duration_minutes"],
        title=item["title"],
        category=BlockCategory(item["category"]),
        reason=item.get("reason", ""),
        evidence_ids=tuple(item.get("evidence_ids") or ()),
        done=bool(item.get("done")),
    )
