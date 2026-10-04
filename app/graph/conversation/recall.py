import logging

from app.domain.conversation.context import RecalledMemory
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.exceptions import LLMError
from app.retrieval.types import RetrievedMemory

logger = logging.getLogger(__name__)


async def embed_or_none(embeddings: EmbeddingProvider, text: str) -> list[float] | None:
    """Embed the text, or report None when the provider cannot.

    None rather than an exception because hybrid retrieval can still search by
    keyword without it: a failed embedding costs recall by meaning, not recall.
    """
    try:
        return await embeddings.embed_query(text)
    except LLMError as error:
        logger.warning("memory_query_embedding_failed", extra={"error_type": type(error).__name__})
        return None


def to_recalled(retrieved: list[RetrievedMemory]) -> list[RecalledMemory]:
    return [
        RecalledMemory(
            id=memory.id,
            content=memory.content,
            memory_type=memory.memory_type.value,
            relevance=memory.relevance,
        )
        for memory in retrieved
    ]
