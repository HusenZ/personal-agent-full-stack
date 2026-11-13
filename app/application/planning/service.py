import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
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
from app.application.runs.recording import build_run_record, store_failed_run
from app.core.context import get_request_id
from app.domain.analysis.evidence import EvidenceBundle, RawEvidence, build_evidence_bundle
from app.domain.planning.week import (
    BlockCategory,
    CheckedWeek,
    PlanBlock,
    ProposedWeek,
    check_week,
    plan_dates,
)
from app.domain.temporal.ranges import resolve_timezone
from app.infrastructure.database.models import AgentRun, RunKind, WeekPlan
from app.infrastructure.database.repositories.onboarding import OnboardingSessionRepository
from app.infrastructure.database.repositories.plan import WeekPlanRepository
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.infrastructure.database.repositories.run import RunRepository
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompt import Prompt
from app.infrastructure.llm.prompts.planning import build_planning_prompt
from app.infrastructure.llm.provider import LLMProvider
from app.infrastructure.observability.tracing import RunTrace, bind_trace, run_step

logger = logging.getLogger(__name__)


class PlanStep(StrEnum):
    """The steps of one week-plan run, in the order they run."""

    COLLECT_EVIDENCE = "collect_evidence"
    PROPOSE_WEEK = "propose_week"
    CHECK_WEEK = "check_week"
    STORE_PLAN = "store_plan"


def describe_plan_step(node: str, update: dict[str, Any] | None) -> dict[str, Any] | None:
    """What a week-plan trace may keep about a step: counts and outcomes only.

    An allow-list, as for conversation turns. Block titles, reasons, the
    summary and the evidence text are what the user said or the model wrote,
    so none of them is stored; categories and rejection reasons are fixed
    vocabularies defined in the domain, so they are.
    """
    if update is None:
        return None
    if node == PlanStep.COLLECT_EVIDENCE:
        evidence: EvidenceBundle = update["evidence"]
        return {"evidence_items": len(evidence.items)}
    if node == PlanStep.PROPOSE_WEEK:
        proposal: ProposedWeek = update["proposal"]
        return {"proposed_blocks": len(proposal.blocks)}
    if node == PlanStep.CHECK_WEEK:
        checked: CheckedWeek = update["checked"]
        categories = Counter(block.category.value for block in checked.blocks)
        return {
            "kept_blocks": len(checked.blocks),
            "rejected": dict(sorted(checked.rejected.items())),
            "categories": dict(sorted(categories.items())),
        }
    if node == PlanStep.STORE_PLAN:
        return {"stored_blocks": len(update["plan"].blocks)}
    return None


@dataclass(frozen=True)
class WeekPlanView:
    id: uuid.UUID
    created_at: datetime
    starts_on: date
    timezone: str
    summary: str
    blocks: tuple[PlanBlock, ...]
    evidence: EvidenceBundle
    # Set only on the response that generated the plan; a plan read later has none.
    run_id: uuid.UUID | None = None


class PlanningService:
    """Plans the user's next seven days from what is known about them.

    One structured model call, then deterministic checks: the model proposes
    blocks, and the domain decides which can go on a calendar. There is no
    retry loop; a week with a few blocks set aside is still a useful week, and
    a failed call is reported rather than replaced with a generic plan.

    Each generation is a run with its own trace, kind ``week_plan``, readable
    through ``/api/v1/runs/{id}/trace`` like a conversation turn. It is stored
    with the plan on success, and on its own when generation fails.
    """

    def __init__(
        self,
        session: AsyncSession,
        plans: WeekPlanRepository,
        profiles: ProfileRepository,
        onboarding_sessions: OnboardingSessionRepository,
        runs: RunRepository,
        llm: LLMProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._plans = plans
        self._profiles = profiles
        self._onboarding_sessions = onboarding_sessions
        self._runs = runs
        self._llm = llm
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def generate(self, user_id: uuid.UUID) -> WeekPlanView:
        trace = RunTrace(run_id=uuid.uuid4(), kind=RunKind.WEEK_PLAN.value)
        with bind_trace(trace):
            try:
                return await self._generate(user_id, trace)
            except Exception as error:
                await self._record_failed_run(user_id, trace, error)
                raise

    async def _generate(self, user_id: uuid.UUID, trace: RunTrace) -> WeekPlanView:
        collected = await run_step(
            PlanStep.COLLECT_EVIDENCE, lambda: self._collect_evidence(user_id)
        )
        raw: RawEvidence = collected["raw"]
        evidence: EvidenceBundle = collected["evidence"]

        profile = await self._profiles.get_by_user_id(user_id)
        zone = resolve_timezone(profile.timezone if profile else None)
        # "This week" starts today where the user is, not where the server is.
        first_day = self._clock().astimezone(zone).date()
        prompt = build_planning_prompt(evidence, plan_dates(first_day))

        proposed = await run_step(PlanStep.PROPOSE_WEEK, lambda: self._propose(user_id, prompt))
        proposal: ProposedWeek = proposed["proposal"]

        checked_step = await run_step(
            PlanStep.CHECK_WEEK,
            lambda: _as_update(checked=check_week(proposal, first_day, evidence.ids)),
        )
        checked: CheckedWeek = checked_step["checked"]

        stored = await run_step(
            PlanStep.STORE_PLAN,
            lambda: self._store(user_id, first_day, zone.key, proposal, checked, raw, prompt),
        )
        plan: WeekPlan = stored["plan"]

        # The plan and the record of the run that made it, together or neither.
        self._runs.add(self._run_record(user_id, trace, error=None))
        await self._session.commit()

        logger.info(
            "week_plan_generated",
            extra={
                "user_id": str(user_id),
                "plan_id": str(plan.id),
                "run_id": str(trace.run_id),
                "block_count": len(checked.blocks),
                "rejected": checked.rejected,
            },
        )
        return replace(_view(plan, evidence), run_id=trace.run_id)

    async def _collect_evidence(self, user_id: uuid.UUID) -> dict[str, Any]:
        raw = await collect_profile_evidence(user_id, self._profiles, self._onboarding_sessions)
        evidence = build_evidence_bundle(raw)
        if evidence.is_empty():
            raise NothingToPlanFromError()
        return {"raw": raw, "evidence": evidence}

    async def _propose(self, user_id: uuid.UUID, prompt: Prompt) -> dict[str, Any]:
        try:
            proposal = await self._llm.generate_structured(prompt, ProposedWeek)
        except LLMError as error:
            logger.warning(
                "week_plan_generation_failed",
                extra={"user_id": str(user_id), "error_type": type(error).__name__},
            )
            raise PlanUnavailableError() from error
        return {"proposal": proposal}

    async def _store(
        self,
        user_id: uuid.UUID,
        first_day: date,
        timezone: str,
        proposal: ProposedWeek,
        checked: CheckedWeek,
        raw: RawEvidence,
        prompt: Prompt,
    ) -> dict[str, Any]:
        if not checked.blocks:
            logger.warning(
                "week_plan_empty", extra={"user_id": str(user_id), "rejected": checked.rejected}
            )
            raise PlanUnavailableError()

        plan = WeekPlan(
            user_id=user_id,
            starts_on=first_day,
            timezone=timezone,
            summary=proposal.summary.strip(),
            blocks=[_serialise_block(block) for block in checked.blocks],
            evidence_snapshot=serialise_evidence(raw),
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            rejected_block_count=sum(checked.rejected.values()),
        )
        self._plans.add(plan)
        return {"plan": plan}

    async def _record_failed_run(
        self, user_id: uuid.UUID, trace: RunTrace, error: BaseException
    ) -> None:
        """Store the run of a plan that was not made, without the plan.

        Best effort, as for conversation turns: when the database is what
        failed this fails too, and the original error is the one that matters.
        """
        await store_failed_run(
            self._session, self._runs, self._run_record(user_id, trace, error=error)
        )

    def _run_record(
        self, user_id: uuid.UUID, trace: RunTrace, *, error: BaseException | None
    ) -> AgentRun:
        proposals = sum(1 for step in trace.steps if step.node == PlanStep.PROPOSE_WEEK)
        return build_run_record(
            trace,
            kind=RunKind.WEEK_PLAN,
            user_id=user_id,
            conversation_id=None,
            request_id=get_request_id(),
            result={"generation_attempts": proposals},
            error=error,
            describe=describe_plan_step,
        )

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


async def _as_update(**values: Any) -> dict[str, Any]:
    """Wrap a synchronous step's result so it is traced like any other step."""
    return values


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
