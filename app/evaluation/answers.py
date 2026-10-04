"""End-to-end answer quality: the real API, the configured model, the dataset.

Deterministic checks come first and carry the most weight: whether the plan
asked for what the question needed, whether exact figures were stated exactly,
whether verification found the reply grounded. A model judge adds the
qualities only a reader can assess, and is reported as a judgement rather than
a measurement: it shares the biases of the model it grades.
"""

import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.evaluation.dataset import CaseCategory, Dataset, EvaluationCase
from app.evaluation.metrics import mean, percentile
from app.evaluation.seeding import SeededPersona, remove_evaluation_users, seed_persona
from app.infrastructure.database.models import AgentRun
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.evaluation import build_judge_prompt
from app.infrastructure.llm.provider import LLMProvider

# Digits only ("23,700", "2.5"); numbers in words are scored as missing.
_NUMBER = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])\d+(?:\.\d+)?")


class AnswerJudgement(BaseModel):
    faithfulness: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    admits_unknown: bool


@dataclass(frozen=True)
class Prices:
    """US dollars per million tokens, for estimating cost per request."""

    input_per_million: float
    output_per_million: float


def numbers_in(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in _NUMBER.findall(text)]


def states_figures(reply: str, figures: list[float]) -> bool:
    stated = numbers_in(reply)
    return all(any(abs(value - figure) < 1e-6 for value in stated) for figure in figures)


async def evaluate_answers(
    app: FastAPI,
    dataset: Dataset,
    *,
    embeddings: EmbeddingProvider,
    embedding_model: str,
    judge: LLMProvider | None = None,
    prices: Prices | None = None,
    run_tag: str | None = None,
) -> dict[str, Any]:
    tag = run_tag or uuid.uuid4().hex[:8]
    now = datetime.now(tz=UTC)
    rows: list[dict[str, Any]] = []

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://evaluation") as client:
            seeded: dict[str, SeededPersona] = {}
            for persona in dataset.personas:
                seeded[persona.key] = await seed_persona(
                    app,
                    client,
                    persona,
                    embeddings=embeddings,
                    embedding_model=embedding_model,
                    run_tag=tag,
                    now=now,
                )

            for case in dataset.cases:
                rows.append(await _run_case(app, client, seeded[case.persona], case, judge))
    finally:
        await remove_evaluation_users(app)

    return {
        "kind": "answers",
        "dataset": dataset.name,
        "dataset_version": dataset.version,
        "dataset_reviewed": dataset.reviewed,
        "created_at": now.isoformat(),
        "config": {"embedding_model": embedding_model, "judged": judge is not None},
        "summary": _summarise(rows, prices),
        "by_category": {
            category.value: _summarise([row for row in rows if row["category"] == category], prices)
            for category in CaseCategory
            if any(row["category"] == category for row in rows)
        },
        "cases": rows,
    }


async def _run_case(
    app: FastAPI,
    client: AsyncClient,
    owner: SeededPersona,
    case: EvaluationCase,
    judge: LLMProvider | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = await client.post(
        "/api/v1/chat", headers=owner.headers, json={"message": case.question}
    )
    wall_ms = (time.perf_counter() - started) * 1000

    row: dict[str, Any] = {
        "case": case.id,
        "category": case.category.value,
        "status_code": response.status_code,
        "wall_ms": round(wall_ms, 2),
    }
    if response.status_code != 201:
        return row | {"failed": True}

    data = response.json()["data"]
    reply: str = data["reply"]["content"]
    run = await _load_run(app, uuid.UUID(data["run_id"]))
    needs = _planned_needs(run)

    row |= {
        "failed": False,
        "degraded": data["degraded"],
        "grounded": data["grounded"],
        "planned_needs": needs,
        "routing_ok": {need.value for need in case.expected_needs} <= set(needs),
        "run_ms": run.duration_ms,
        "model_calls": run.model_calls,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "retrieval_attempts": run.retrieval_attempts,
        "generation_attempts": run.generation_attempts,
    }
    if case.expected_figures:
        row["figures_ok"] = states_figures(reply, case.expected_figures)

    if judge is not None:
        row |= await _judge(judge, owner, case, reply)

    return row


async def _judge(
    judge: LLMProvider, owner: SeededPersona, case: EvaluationCase, reply: str
) -> dict[str, Any]:
    by_key = {memory.key: memory.content for memory in owner.persona.memories}
    facts = [by_key[key] for key in case.relevant]
    try:
        verdict = await judge.generate_structured(
            build_judge_prompt(case.question, facts, case.expected_figures, reply),
            AnswerJudgement,
        )
    except LLMError as error:
        return {"judge_error": type(error).__name__}
    return {"judgement": verdict.model_dump()}


async def _load_run(app: FastAPI, run_id: uuid.UUID) -> AgentRun:
    async with app.state.database_session_factory() as session:
        run: AgentRun = (await session.scalars(select(AgentRun).where(AgentRun.id == run_id))).one()
        return run


def _planned_needs(run: AgentRun) -> list[str]:
    for step in run.steps:
        if step["node"] == "understand_query":
            return list(step["detail"].get("needs") or [])
    return []


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [bool(row[key]) for row in rows if row.get(key) is not None]
    return round(sum(values) / len(values), 4) if values else None


def _summarise(rows: list[dict[str, Any]], prices: Prices | None) -> dict[str, Any]:
    answered = [row for row in rows if not row.get("failed")]
    summary: dict[str, Any] = {
        "cases": len(rows),
        "error_rate": round(1 - len(answered) / len(rows), 4) if rows else None,
        "degraded_rate": _rate(answered, "degraded"),
        "grounded_rate": _rate(answered, "grounded"),
        "routing_accuracy": _rate(answered, "routing_ok"),
        "figure_accuracy": _rate(answered, "figures_ok"),
    }
    if answered:
        # The reply streams after verification, so first token equals the full turn.
        wall = [row["wall_ms"] for row in answered]
        summary |= {
            "latency_p50_ms": round(percentile(wall, 50), 2),
            "latency_p95_ms": round(percentile(wall, 95), 2),
            "model_calls_mean": round(mean([row["model_calls"] for row in answered]), 2),
        }
        tokens = [
            (row["input_tokens"], row["output_tokens"])
            for row in answered
            if row["input_tokens"] is not None and row["output_tokens"] is not None
        ]
        if tokens:
            summary["input_tokens_mean"] = round(mean([pair[0] for pair in tokens]), 1)
            summary["output_tokens_mean"] = round(mean([pair[1] for pair in tokens]), 1)
            if prices is not None:
                cost = [
                    pair[0] / 1e6 * prices.input_per_million
                    + pair[1] / 1e6 * prices.output_per_million
                    for pair in tokens
                ]
                summary["cost_per_request_usd"] = round(mean(cost), 6)

    judged = [row["judgement"] for row in answered if "judgement" in row]
    if judged:
        for quality in ("faithfulness", "relevance", "completeness"):
            summary[f"judge_{quality}_mean"] = round(mean([j[quality] for j in judged]), 3)
        unanswerable = [
            row["judgement"]["admits_unknown"]
            for row in answered
            if "judgement" in row and row["category"] == CaseCategory.UNANSWERABLE
        ]
        if unanswerable:
            summary["admits_unknown_rate"] = round(sum(unanswerable) / len(unanswerable), 4)
    return summary
