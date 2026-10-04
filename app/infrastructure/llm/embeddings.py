import asyncio
import logging
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from app.core.config import Settings
from app.infrastructure.llm.exceptions import (
    LLMConfigurationError,
    LLMInvalidOutputError,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)

# Fixed in the schema as vector(1536); changing it needs a migration.
EMBEDDING_DIMENSIONS = 1536

_RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
    TimeoutError,
)
_CREDENTIAL_ERRORS = (AuthenticationError, PermissionDeniedError)


class EmbeddingProvider(Protocol):
    """Turns text into vectors for semantic comparison.

    Separate from LLMProvider because the two fail, cost and scale differently:
    embeddings are cheap, batched, and called far more often.
    """

    async def embed_query(self, text: str) -> list[float]:
        """Embed one piece of text that is being searched *with*."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed several pieces of text that are being searched *over*."""
        ...


class OpenAIEmbeddingProvider:
    """EmbeddingProvider backed by OpenAI through LangChain.

    The client is built on first use so an application without credentials still
    starts, and only fails where an embedding is actually needed.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    def _embeddings(self) -> Any:
        if self._client is not None:
            return self._client

        if self._settings.openai_api_key is None:
            raise LLMConfigurationError("OPENAI_API_KEY is not configured")

        from langchain_openai import OpenAIEmbeddings

        # Field names, not aliases, so the call type-checks.
        self._client = OpenAIEmbeddings(
            model=self._settings.embedding_model,
            openai_api_key=self._settings.openai_api_key,
            dimensions=EMBEDDING_DIMENSIONS,
            request_timeout=self._settings.embedding_timeout_seconds,
            # We retry ourselves so attempts use our timeout and logging.
            max_retries=0,
        )
        return self._client

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._embeddings()
        last_error: Exception | None = None

        for attempt in range(1, self._settings.llm_max_attempts + 1):
            try:
                async with asyncio.timeout(self._settings.embedding_timeout_seconds):
                    vectors = await client.aembed_documents(texts)
            except _CREDENTIAL_ERRORS as error:
                logger.error("embedding_credentials_rejected")
                raise LLMConfigurationError(
                    "the configured OpenAI credentials were rejected"
                ) from error
            except BadRequestError as error:
                raise LLMInvalidOutputError(f"provider rejected the request: {error}") from error
            except _RETRYABLE as error:
                last_error = error
                logger.warning(
                    "embedding_request_failed",
                    extra={
                        "attempt": attempt,
                        "max_attempts": self._settings.llm_max_attempts,
                        "error_type": type(error).__name__,
                        "batch_size": len(texts),
                    },
                )
                continue

            # A wrong-sized vector would be rejected by pgvector anyway.
            if len(vectors) != len(texts) or any(
                len(vector) != EMBEDDING_DIMENSIONS for vector in vectors
            ):
                raise LLMInvalidOutputError(
                    f"expected {len(texts)} vectors of {EMBEDDING_DIMENSIONS} dimensions"
                )

            return [list(vector) for vector in vectors]

        raise LLMUnavailableError(
            f"embedding failed after {self._settings.llm_max_attempts} attempts"
        ) from last_error
