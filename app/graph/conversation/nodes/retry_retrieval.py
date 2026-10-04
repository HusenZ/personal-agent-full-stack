import logging
import time
import uuid

from app.domain.retrieval.grading import EvidencePolicy, merge_memories, next_search_query
from app.graph.conversation.recall import embed_or_none, to_recalled
from app.graph.conversation.state import ConversationNode, ConversationTurnState
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.retrieval.hybrid import MemoryRetriever

logger = logging.getLogger(__name__)


def build_retry_retrieval_node(
    embeddings: EmbeddingProvider, retriever: MemoryRetriever, policy: EvidencePolicy
) -> ConversationNode:
    async def retry_retrieval(state: ConversationTurnState) -> ConversationTurnState:
        """Search memory again with the grader's rewritten query.

        Only memory is searched again. The timeline and totals are computed from
        the plan's period and activities, not from the wording of the question,
        so running them with different words would return the same rows.
        """
        searches = state.get("searches") or []
        attempt = (state.get("retrieval_attempts") or 0) + 1
        user_id_value = state.get("user_id")
        query = next_search_query(state.get("evidence_grade"), searches)

        # Count the attempt anyway so the loop always moves towards its bound.
        if query is None or not user_id_value:
            return {"retrieval_attempts": attempt}

        errors: list[str] = []
        started = time.perf_counter()

        embedding = await embed_or_none(embeddings, query)
        if embedding is None:
            errors.append("query_embedding_failed")

        retrieved = await retriever.retrieve(uuid.UUID(user_id_value), query, embedding)
        found = to_recalled(retrieved)
        merged = merge_memories(
            state.get("memories") or [], found, limit=policy.max_graded_memories
        )

        logger.info(
            "memory_retrieval_retried",
            extra={
                "user_id": user_id_value,
                "conversation_id": state.get("conversation_id"),
                "attempt": attempt,
                "found": len(found),
                "candidates": len(merged),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

        return {
            "memories": merged,
            "searches": [*searches, query],
            "retrieval_attempts": attempt,
            "errors": [*(state.get("errors") or []), *errors],
        }

    return retry_retrieval
