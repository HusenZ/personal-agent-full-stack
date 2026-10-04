import logging

from app.graph.memory.state import MemoryExtractionState, MemoryNode
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.exceptions import LLMError

logger = logging.getLogger(__name__)


def build_embed_candidates_node(embeddings: EmbeddingProvider) -> MemoryNode:
    async def embed_candidates(state: MemoryExtractionState) -> MemoryExtractionState:
        """Embed the surviving candidates once, for comparison and for storage.

        Batched into one call: embedding is cheap per item and expensive per
        round trip. Runs after filtering so nothing already rejected is paid for.
        """
        admitted = state.get("admitted") or []
        if not admitted:
            return {"embeddings": []}

        try:
            vectors = await embeddings.embed_documents(
                [candidate.content for candidate in admitted]
            )
        except LLMError as error:
            # Stored without a vector: kept and listed, but not yet found by meaning.
            logger.warning(
                "memory_embedding_failed",
                extra={"error_type": type(error).__name__, "batch_size": len(admitted)},
            )
            return {"embeddings": [None] * len(admitted)}

        return {"embeddings": [tuple(vector) for vector in vectors]}

    return embed_candidates
