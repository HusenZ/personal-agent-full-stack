"""Where a conversation turn spends its time, apart from the model.

Runs the real application against a real database with a deterministic fake
model, seeds a user with a realistic amount of history, sends a series of
turns, and reports per-step latency from the stored run traces. Model latency
is excluded by construction: this measures what the system itself costs, which
is what caching or query changes could affect.

    uv run python -m scripts.benchmark_turn [--turns 40] [--seed-turns 25]

Needs the database from docker-compose (or TEST_DATABASE_URL), migrated.
"""

import argparse
import asyncio
import os
import random
import statistics
import time
import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.application.conversation.service import ConversationService
from app.core.config import Settings
from app.domain.memory.types import (
    MemoryCandidate,
    MemoryDurability,
    MemoryExtraction,
    MemoryType,
)
from app.domain.retrieval.plan import QueryPlan, RetrievalNeed
from app.domain.temporal.ranges import TimeRangeSpec
from app.infrastructure.database.models import AgentRun, Event, Memory
from app.main import create_app
from app.workers.runner import build_worker
from tests.fakes import FakeEmbeddingProvider, FakeLLMProvider

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://pia:pia@localhost:5433/pia"
)
PASSWORD = "a-sufficiently-long-password"
TOPICS = ["running", "study", "reading", "cooking", "piano", "work", "sleep", "travel"]

# Distinct words per memory so deduplication keeps them apart.
_RANDOM = random.Random(7)
VOCABULARY = ["".join(_RANDOM.choices("abcdefghijklmnopqrstuvwxyz", k=7)) for _ in range(5000)]


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def candidates(turn: int) -> list[MemoryCandidate]:
    made = []
    for index, topic in enumerate(TOPICS):
        is_event = index % 3 == 0
        made.append(
            MemoryCandidate(
                content=f"I {topic} " + " ".join(_RANDOM.sample(VOCABULARY, 10)),
                memory_type=MemoryType.EVENT if is_event else MemoryType.FACT,
                durability=MemoryDurability.LONG_TERM,
                importance=0.6,
                confidence=0.9,
                activity=topic if is_event else None,
                quantity=float(index + 1) if is_event else None,
                unit="hours" if is_event else None,
                days_ago=turn % 14 if is_event else None,
            )
        )
    return made


async def run(turns: int, seed_turns: int) -> None:
    settings = Settings(
        environment="test",
        log_level="ERROR",
        log_format="console",
        database_url=DATABASE_URL,
        jwt_secret="benchmark-signing-key-that-is-long-enough",
        rate_limit_enabled=False,
        usage_limits_enabled=False,
        embedding_model="test-embedding-model",
    )
    model = FakeLLMProvider(replies=["A considered reply."])
    app = create_app(settings, llm_provider=model, embedding_provider=FakeEmbeddingProvider())
    worker = build_worker(
        settings,
        app.state.database_session_factory,
        app.state.memory_graph,
        app.state.embedding_provider,
    )

    prepare_ms: list[float] = []
    original_prepare = ConversationService.prepare_turn

    async def timed_prepare(self: ConversationService, command: Any) -> Any:
        started = time.perf_counter()
        prepared = await original_prepare(self, command)
        prepare_ms.append((time.perf_counter() - started) * 1000)
        return prepared

    ConversationService.prepare_turn = timed_prepare  # type: ignore[method-assign]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://benchmark") as client:
        email = f"benchmark-{uuid.uuid4().hex[:8]}@example.com"
        await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        await client.patch(
            "/api/v1/profile",
            headers=headers,
            json={
                "timezone": "Europe/London",
                "goals": [{"name": f"Get better at {t}", "priority": "high"} for t in TOPICS[:5]],
                "constraints": ["Only evenings are free", "A bad knee"],
                "preferences": {"tone": "direct", "length": "short"},
            },
        )

        for turn in range(seed_turns):
            model.queue_memory_extractions(MemoryExtraction(candidates=candidates(turn)))
            await client.post("/api/v1/chat", headers=headers, json={"message": f"Seed {turn}"})
            while await worker.run_once():
                pass
        model.queue_memory_extractions(MemoryExtraction())

        # The widest plan: every source, so retrieval is measured at its most expensive.
        model.queue_plans(
            QueryPlan(
                needs=[RetrievalNeed.SEMANTIC, RetrievalNeed.TIMELINE, RetrievalNeed.TOTALS],
                time_range=TimeRangeSpec.LAST_7_DAYS,
                activities=["running", "study"],
            )
        )
        prepare_ms.clear()
        conversation_id: str | None = None
        run_ids: list[uuid.UUID] = []
        for turn in range(turns):
            payload: dict[str, Any] = {"message": f"How did running and study go? ({turn})"}
            if conversation_id is not None:
                payload["conversation_id"] = conversation_id
            data = (await client.post("/api/v1/chat", headers=headers, json=payload)).json()["data"]
            conversation_id = data["conversation_id"]
            run_ids.append(uuid.UUID(data["run_id"]))

    async with app.state.database_session_factory() as session:
        memories = await session.scalar(select(func.count()).select_from(Memory))
        events = await session.scalar(select(func.count()).select_from(Event))
        runs = (await session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))).all()
    await app.state.database_engine.dispose()

    by_step: dict[str, list[float]] = {"prepare_turn (outside the graph)": prepare_ms}
    for run_record in runs:
        for step in run_record.steps:
            by_step.setdefault(step["node"], []).append(step["duration_ms"])
    by_step["whole turn (graph only)"] = [run_record.duration_ms for run_record in runs]

    print(f"user history: {memories} memories, {events} events; {len(runs)} measured turns")
    print(f"{'step':<36}{'p50 ms':>9}{'p95 ms':>9}")
    for name, values in by_step.items():
        print(f"{name:<36}{statistics.median(values):>9.2f}{percentile(values, 95):>9.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--turns", type=int, default=40)
    parser.add_argument("--seed-turns", type=int, default=25)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.turns, arguments.seed_turns))


if __name__ == "__main__":
    main()
