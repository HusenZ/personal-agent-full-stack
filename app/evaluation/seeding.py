import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, select

from app.domain.memory.types import MemoryDurability, MemorySource, MemoryType
from app.domain.temporal.ranges import local_midday
from app.evaluation.dataset import Persona
from app.infrastructure.database.models import Event, Memory, User
from app.infrastructure.llm.embeddings import EmbeddingProvider

# RFC 2606 reserved domain, so it can never collide with a real user.
EVALUATION_EMAIL_DOMAIN = "evaluation.example.com"
# Protects synthetic accounts holding synthetic data, deleted when the run ends.
_PASSWORD = "evaluation-only-password-not-a-secret"  # noqa: S105


@dataclass(frozen=True)
class SeededPersona:
    persona: Persona
    user_id: uuid.UUID
    headers: dict[str, str]
    # Dataset memory key to the id it was stored under, and back.
    memory_ids: dict[str, uuid.UUID]

    def key_of(self, memory_id: uuid.UUID) -> str | None:
        return next((key for key, value in self.memory_ids.items() if value == memory_id), None)


async def seed_persona(
    app: FastAPI,
    client: AsyncClient,
    persona: Persona,
    *,
    embeddings: EmbeddingProvider,
    embedding_model: str,
    run_tag: str,
    now: datetime | None = None,
) -> SeededPersona:
    """Create a persona's account, profile, memories and events.

    The account and profile go through the API, as a real user's would. Memories
    and events are written directly, because creating them through extraction
    would make the dataset depend on the model being evaluated.
    """
    moment = now or datetime.now(tz=UTC)
    email = f"{persona.key}-{run_tag}@{EVALUATION_EMAIL_DOMAIN}"

    registered = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": _PASSWORD}
    )
    registered.raise_for_status()
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": _PASSWORD})
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    profile = await client.patch(
        "/api/v1/profile",
        headers=headers,
        json={
            "timezone": persona.timezone,
            "goals": [{"name": goal, "priority": "high"} for goal in persona.goals],
            "constraints": persona.constraints,
            "preferences": persona.preferences,
        },
    )
    profile.raise_for_status()

    vectors = (
        await embeddings.embed_documents([memory.content for memory in persona.memories])
        if persona.memories
        else []
    )

    async with app.state.database_session_factory() as session:
        user_id = (await session.scalars(select(User.id).where(User.email == email))).one()

        memory_ids: dict[str, uuid.UUID] = {}
        for memory, vector in zip(persona.memories, vectors, strict=True):
            said_at = moment - timedelta(days=memory.days_ago)
            row = Memory(
                # Deterministic ids keep score tie-breaks stable between runs.
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"{run_tag}/{persona.key}/{memory.key}"),
                user_id=user_id,
                content=memory.content,
                memory_type=memory.memory_type,
                durability=MemoryDurability.LONG_TERM,
                source=MemorySource.MANUAL,
                importance=memory.importance,
                confidence=0.9,
                occurred_at=said_at if memory.memory_type is MemoryType.EVENT else None,
                embedding=vector,
                embedding_model=embedding_model,
                created_at=said_at,
            )
            session.add(row)
            memory_ids[memory.key] = row.id

        for event in persona.events:
            session.add(
                Event(
                    user_id=user_id,
                    activity=event.activity,
                    description=event.description,
                    occurred_at=local_midday(event.days_ago, moment, persona.timezone),
                    quantity=event.quantity,
                    unit=event.unit,
                )
            )

        await session.commit()

    return SeededPersona(persona=persona, user_id=user_id, headers=headers, memory_ids=memory_ids)


async def remove_evaluation_users(app: FastAPI) -> int:
    """Delete every evaluation account, and with it everything it owns."""
    async with app.state.database_session_factory() as session:
        result = await session.execute(
            delete(User).where(User.email.like(f"%@{EVALUATION_EMAIL_DOMAIN}")).returning(User.id)
        )
        removed = len(result.all())
        await session.commit()
    return removed
