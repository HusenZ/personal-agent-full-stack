"""What a week-plan trace may keep about each step: counts and outcomes, never content."""

import json
import uuid
from datetime import date

import pytest

from app.application.planning.service import PlanStep, describe_plan_step
from app.application.runs.recording import build_run_record
from app.domain.analysis.evidence import EvidenceBundle, EvidenceItem, EvidenceKind
from app.domain.planning.week import BlockCategory, ProposedBlock, ProposedWeek, check_week
from app.infrastructure.database.models import RunKind, RunStatus
from app.infrastructure.observability.tracing import RunTrace, bind_trace, run_step

FIRST_DAY = date(2026, 10, 3)
SECRET_TITLE = "Call my therapist about the divorce"
SECRET_REASON = "You said the separation is weighing on you."


def proposal() -> ProposedWeek:
    return ProposedWeek(
        summary="A private summary.",
        blocks=[
            ProposedBlock(
                day=0,
                start="09:00",
                duration_minutes=30,
                title=SECRET_TITLE,
                category=BlockCategory.SOCIAL,
                reason=SECRET_REASON,
                evidence_ids=["goal:1", "goal:99"],
            ),
            ProposedBlock(
                day=0,
                start="09:15",
                duration_minutes=30,
                title="Overlapping block",
                category=BlockCategory.HEALTH,
                reason="Clashes with the first.",
                evidence_ids=["goal:1"],
            ),
            ProposedBlock(
                day=3,
                duration_minutes=20,
                title="Stretch",
                category=BlockCategory.HEALTH,
                reason="Recovery.",
                evidence_ids=["goal:1"],
            ),
        ],
    )


class TestDescribingSteps:
    def test_evidence_is_counted_not_copied(self):
        evidence = EvidenceBundle(
            items=(EvidenceItem(id="goal:1", kind=EvidenceKind.GOAL, content=SECRET_TITLE),)
        )

        detail = describe_plan_step(PlanStep.COLLECT_EVIDENCE, {"evidence": evidence})

        assert detail == {"evidence_items": 1}

    def test_a_proposal_is_counted_not_copied(self):
        detail = describe_plan_step(PlanStep.PROPOSE_WEEK, {"proposal": proposal()})

        assert detail == {"proposed_blocks": 3}

    def test_checking_reports_kept_rejected_and_categories(self):
        checked = check_week(proposal(), FIRST_DAY, frozenset({"goal:1"}))

        detail = describe_plan_step(PlanStep.CHECK_WEEK, {"checked": checked})

        assert detail == {
            "kept_blocks": 2,
            "rejected": {"overlap": 1, "unknown_evidence": 1},
            "categories": {"health": 1, "social": 1},
        }

    def test_categories_are_the_enum_values_a_client_sees(self):
        checked = check_week(proposal(), FIRST_DAY, frozenset({"goal:1"}))

        detail = describe_plan_step(PlanStep.CHECK_WEEK, {"checked": checked})

        assert detail is not None
        assert set(detail["categories"]) <= {category.value for category in BlockCategory}

    @pytest.mark.parametrize("node", ["generate_reply", "anything_else"])
    def test_an_unknown_step_keeps_nothing(self, node: str):
        assert describe_plan_step(node, {"proposal": proposal()}) is None

    def test_a_step_without_output_keeps_nothing(self):
        assert describe_plan_step(PlanStep.CHECK_WEEK, None) is None


class TestRecordingARun:
    async def test_the_stored_record_holds_no_plan_content(self):
        week = proposal()
        with bind_trace(RunTrace(run_id=uuid.uuid4())) as trace:
            await run_step(PlanStep.PROPOSE_WEEK, _returning({"proposal": week}))
            checked = check_week(week, FIRST_DAY, frozenset({"goal:1"}))
            await run_step(PlanStep.CHECK_WEEK, _returning({"checked": checked}))

        record = build_run_record(
            trace,
            kind=RunKind.WEEK_PLAN,
            user_id=uuid.uuid4(),
            conversation_id=None,
            request_id=None,
            result={"generation_attempts": 1},
            error=None,
            describe=describe_plan_step,
        )

        assert record.kind is RunKind.WEEK_PLAN
        assert record.status is RunStatus.COMPLETED
        assert [step["node"] for step in record.steps] == ["propose_week", "check_week"]
        stored = json.dumps(record.steps)
        for written in (SECRET_TITLE, SECRET_REASON, week.summary, "Stretch"):
            assert written not in stored


def _returning(value: dict[str, object]):
    async def call() -> dict[str, object]:
        return value

    return call
