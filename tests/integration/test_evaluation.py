"""The evaluation harness against real PostgreSQL, with deterministic fakes.

With fake embeddings the retrieval numbers describe word overlap, not meaning,
so they are a regression check on retrieval code, not a verdict on strategies.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.domain.temporal.ranges import TimeRangeSpec, resolve_range, resolve_timezone
from app.evaluation.answers import AnswerJudgement, evaluate_answers
from app.evaluation.dataset import CaseCategory, load_dataset
from app.evaluation.report import compare_reports, write_report
from app.evaluation.retrieval import evaluate_retrieval
from app.evaluation.seeding import EVALUATION_EMAIL_DOMAIN, seed_persona
from app.infrastructure.database.models import User
from app.retrieval.hybrid import HybridConfig
from app.retrieval.structured import StructuredActivityQuery
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider

pytestmark = pytest.mark.integration

DATASET = load_dataset("personas-v1")
BASELINE = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "results"
    / "baseline-retrieval-fake-embeddings.json"
)
REPLY = "You ran 25 km over 3 runs."


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider(
        replies=[REPLY],
        judgements=[
            AnswerJudgement(faithfulness=5, relevance=4, completeness=3, admits_unknown=False)
        ],
    )


async def evaluation_users(app: FastAPI) -> int:
    async with app.state.database_session_factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.email.like(f"%@{EVALUATION_EMAIL_DOMAIN}"))
            )
            or 0
        )


class TestDatasetIntegrity:
    async def test_every_expected_figure_is_what_the_database_computes(self, api_app: FastAPI):
        """A wrong label would mark correct answers as wrong."""
        now = datetime.now(tz=UTC)
        transport = ASGITransport(app=api_app)
        async with AsyncClient(transport=transport, base_url="http://evaluation") as client:
            for persona in DATASET.personas:
                cases = [
                    case
                    for case in DATASET.cases
                    if case.persona == persona.key and case.expected_figures
                ]
                if not cases:
                    continue
                seeded = await seed_persona(
                    api_app,
                    client,
                    persona,
                    embeddings=FakeEmbeddingProvider(),
                    embedding_model="test-embedding-model",
                    run_tag="integrity",
                    now=now,
                )
                time_range = resolve_range(
                    TimeRangeSpec.LAST_7_DAYS, now, resolve_timezone(persona.timezone)
                )
                summary = await StructuredActivityQuery(
                    api_app.state.database_session_factory
                ).summarise(seeded.user_id, time_range)
                computed = {total.total_quantity for total in summary.totals}

                for case in cases:
                    for figure in case.expected_figures:
                        assert figure in computed, f"{case.id}: {figure} not in {computed}"


class TestRetrievalRegression:
    async def report(self, app: FastAPI) -> dict[str, Any]:
        return await evaluate_retrieval(
            app,
            DATASET,
            embeddings=FakeEmbeddingProvider(),
            embedding_model="test-embedding-model",
            config=HybridConfig.for_limit(6, 0.25),
            run_tag="regression",
        )

    async def test_no_strategy_falls_below_the_committed_baseline(self, api_app: FastAPI):
        report = await self.report(api_app)
        # Timings vary from run to run; only the quality metrics are pinned.
        report["summary"] = {
            strategy: {key: value for key, value in metrics.items() if "latency" not in key}
            for strategy, metrics in report["summary"].items()
        }
        pinned = {key: report[key] for key in ("kind", "dataset", "dataset_version", "config")}
        pinned["summary"] = report["summary"]

        if os.environ.get("UPDATE_EVALUATION_BASELINE") == "1":
            write_report(pinned | {"dataset_reviewed": report["dataset_reviewed"]}, BASELINE)

        baseline = json.loads(BASELINE.read_text())
        worse = [row for row in compare_reports(baseline, pinned) if row["verdict"] == "worse"]

        assert worse == [], (
            "retrieval quality fell below the baseline; if the change is intended, "
            "rerun with UPDATE_EVALUATION_BASELINE=1 and commit the new baseline"
        )

    async def test_every_strategy_is_scored_on_every_answerable_case(self, api_app: FastAPI):
        report = await self.report(api_app)

        answerable = [case for case in DATASET.cases if case.relevant]
        for metrics in report["summary"].values():
            assert metrics["cases"] == len(answerable)

    async def test_the_synthetic_users_are_removed_afterwards(self, api_app: FastAPI):
        await self.report(api_app)

        assert await evaluation_users(api_app) == 0


class TestAnswers:
    async def report(self, app: FastAPI, judge: FakeLLMProvider | None) -> dict[str, Any]:
        return await evaluate_answers(
            app,
            DATASET,
            embeddings=FakeEmbeddingProvider(),
            embedding_model="test-embedding-model",
            judge=judge,
            run_tag="answers",
        )

    async def test_every_case_is_answered_through_the_api(self, api_app: FastAPI):
        report = await self.report(api_app, judge=None)

        assert report["summary"]["cases"] == len(DATASET.cases)
        assert report["summary"]["error_rate"] == 0
        assert report["summary"]["model_calls_mean"] > 0

    async def test_routing_is_read_from_the_run_trace(self, api_app: FastAPI):
        """The fake plans semantic recall for everything, so only temporal cases miss."""
        report = await self.report(api_app, judge=None)

        by_case = {row["case"]: row for row in report["cases"]}
        assert by_case["maya-knee"]["routing_ok"] is True
        assert by_case["maya-run-distance"]["routing_ok"] is False
        assert by_case["maya-run-distance"]["planned_needs"] == ["semantic"]
        temporal = sum(1 for case in DATASET.cases if case.category is CaseCategory.TEMPORAL)
        expected = round(1 - temporal / len(DATASET.cases), 4)
        assert report["summary"]["routing_accuracy"] == expected

    async def test_figures_are_checked_only_where_the_dataset_expects_them(self, api_app: FastAPI):
        report = await self.report(api_app, judge=None)

        by_case = {row["case"]: row for row in report["cases"]}
        assert by_case["maya-run-distance"]["figures_ok"] is True
        assert by_case["jun-steps"]["figures_ok"] is False
        assert "figures_ok" not in by_case["maya-knee"]

    async def test_judgements_are_aggregated_when_a_judge_is_given(
        self, api_app: FastAPI, llm_provider: FakeLLMProvider
    ):
        report = await self.report(api_app, judge=llm_provider)

        summary = report["summary"]
        assert summary["judge_faithfulness_mean"] == 5
        assert summary["judge_completeness_mean"] == 3
        assert summary["admits_unknown_rate"] == 0
        judge_prompt = llm_provider.prompts_named("evaluation.judge")[0].user
        assert REPLY in judge_prompt

    async def test_the_synthetic_users_are_removed_afterwards(self, api_app: FastAPI):
        await self.report(api_app, judge=None)

        assert await evaluation_users(api_app) == 0
