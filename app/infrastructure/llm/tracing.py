from pydantic import BaseModel

from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.prompt import Prompt
from app.infrastructure.llm.provider import LLMProvider
from app.infrastructure.observability.tracing import ModelCallKind, model_call


class TracingLLMProvider:
    """Records every model call into the current run's trace.

    A decorator rather than code inside each adapter, so every provider is
    traced the same way, including the fakes the test suite runs on. Token
    usage is the exception: only an adapter can see it, and reports it through
    ``record_token_usage`` while this call is open.
    """

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    async def generate_structured[ModelT: BaseModel](
        self, prompt: Prompt, schema: type[ModelT]
    ) -> ModelT:
        async with model_call(ModelCallKind.STRUCTURED, prompt.name, prompt.version):
            return await self._inner.generate_structured(prompt, schema)

    async def generate_text(self, prompt: Prompt) -> str:
        async with model_call(ModelCallKind.COMPLETION, prompt.name, prompt.version):
            return await self._inner.generate_text(prompt)


class TracingEmbeddingProvider:
    """Records every embedding call into the current run's trace."""

    def __init__(self, inner: EmbeddingProvider) -> None:
        self._inner = inner

    async def embed_query(self, text: str) -> list[float]:
        async with model_call(ModelCallKind.EMBEDDING, "embedding.query"):
            return await self._inner.embed_query(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        async with model_call(ModelCallKind.EMBEDDING, "embedding.documents"):
            return await self._inner.embed_documents(texts)
