import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

from app.domain.conversation.context import RecalledMemory
from app.domain.retrieval.plan import QueryPlan
from app.domain.temporal.ranges import resolve_range, resolve_timezone
from app.graph.conversation.recall import embed_or_none, to_recalled
from app.graph.conversation.state import ConversationNode, ConversationTurnState
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.retrieval.hybrid import MemoryRetriever
from app.retrieval.structured import ActivitySummary, StructuredActivityQuery
from app.retrieval.temporal import TemporalEventRetriever, TimelineEntry


def build_retrieve_context_node(
    embeddings: EmbeddingProvider,
    retriever: MemoryRetriever,
    timeline: TemporalEventRetriever | None,
    totals: StructuredActivityQuery | None,
    *,
    timeline_limit: int,
) -> ConversationNode:
    async def retrieve_context(state: ConversationTurnState) -> ConversationTurnState:
        """Gather everything the plan asked for.

        Concurrency is used where it pays and nowhere else. The embedding call
        goes to a different service from the database, so it is started first
        and overlaps with the database work. The database queries themselves run
        one after another. Each has its own short session, so they could run
        concurrently, but that would hold up to four pooled connections per turn
        to save part of a step measured at about 13 ms, on a turn dominated by
        model calls: the wrong trade while connections are the scarce resource.
        """
        message = state.get("message") or ""
        user_id_value = state.get("user_id")
        plan = state.get("plan") or QueryPlan()

        if not message or not user_id_value:
            return {"memories": [], "timeline": [], "totals": None}

        user_id = uuid.UUID(user_id_value)
        now = datetime.now(tz=UTC)
        time_range = resolve_range(plan.time_range, now, resolve_timezone(state.get("timezone")))
        activities = tuple(plan.activities)

        embedding_task = (
            asyncio.create_task(embed_or_none(embeddings, message))
            if plan.needs_semantic()
            else None
        )
        if embedding_task is not None:
            # Yield once so the embedding request starts now, overlapping the DB work.
            await asyncio.sleep(0)

        try:
            entries: list[TimelineEntry] = []
            if plan.needs_timeline() and timeline is not None:
                entries = await timeline.timeline(
                    user_id, time_range, activities=activities, limit=timeline_limit
                )

            summary: ActivitySummary | None = None
            if plan.needs_totals() and totals is not None:
                summary = await totals.summarise(user_id, time_range, activities=activities)
        except BaseException:
            # Cancel and await the embedding call before the error propagates.
            if embedding_task is not None:
                embedding_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await embedding_task
            raise

        recalled: list[RecalledMemory] = []
        searches: list[str] = []
        errors: list[str] = []

        if embedding_task is not None:
            query_embedding = await embedding_task
            if query_embedding is None:
                # Keyword search still works without the embedding.
                errors.append("query_embedding_failed")

            retrieved = await retriever.retrieve(user_id, message, query_embedding, now=now)
            recalled = to_recalled(retrieved)
            searches = [message]

        return {
            "memories": recalled,
            "timeline": entries,
            "totals": summary,
            "time_range": time_range,
            "searches": searches,
            "retrieval_attempts": len(searches),
            "errors": [*(state.get("errors") or []), *errors],
        }

    return retrieve_context
