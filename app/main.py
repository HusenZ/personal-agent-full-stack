import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import health
from app.api.errors import register_exception_handlers
from app.api.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.domain.analysis.policy import DEFAULT_ANALYSIS_POLICY
from app.domain.conversation.context import DEFAULT_CONTEXT_BUDGET
from app.domain.memory.policy import DEFAULT_MEMORY_POLICY
from app.domain.onboarding.policy import DEFAULT_ONBOARDING_POLICY
from app.graph.analysis.graph import build_analysis_graph
from app.graph.conversation.graph import ConversationGraph, build_conversation_graph
from app.graph.memory.graph import build_memory_graph
from app.graph.onboarding.graph import build_onboarding_graph
from app.infrastructure.cache.rate_limiter import RateLimiter
from app.infrastructure.cache.redis import create_redis_client
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.infrastructure.llm.embeddings import (
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from app.infrastructure.llm.openai_provider import OpenAIStructuredProvider
from app.infrastructure.llm.provider import LLMProvider
from app.infrastructure.llm.tracing import TracingEmbeddingProvider, TracingLLMProvider
from app.infrastructure.observability.logging import configure_logging
from app.retrieval.hybrid import HybridConfig, HybridMemoryRetriever
from app.retrieval.keyword import KeywordMemoryRetriever
from app.retrieval.semantic import SemanticMemoryRetriever
from app.retrieval.structured import StructuredActivityQuery
from app.retrieval.temporal import TemporalEventRetriever

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    llm_provider: LLMProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> FastAPI:
    """Build the ASGI application.

    Settings are injectable so tests can exercise environment-dependent behaviour
    without mutating process environment variables.

    The model provider is injectable for the same reason, and because it is the
    single seam every graph is built through: swapping it here keeps a test off
    the network without having to remember each graph individually.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    engine = create_database_engine(settings)

    # Wrap once so every graph's model calls are traced.
    provider: LLMProvider = TracingLLMProvider(llm_provider or OpenAIStructuredProvider(settings))
    embeddings: EmbeddingProvider = TracingEmbeddingProvider(
        embedding_provider or OpenAIEmbeddingProvider(settings)
    )
    redis_client = create_redis_client(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application_started",
            extra={"environment": settings.environment, "version": settings.app_version},
        )
        try:
            yield
        finally:
            if redis_client is not None:
                await redis_client.aclose()
            await engine.dispose()
            logger.info("application_stopped")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        # No API docs in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.state.settings = settings
    app.state.database_engine = engine
    app.state.database_session_factory = create_session_factory(engine)
    app.state.llm_provider = provider
    app.state.embedding_provider = embeddings
    app.state.redis_client = redis_client
    app.state.rate_limiter = RateLimiter(redis_client, fail_open=settings.rate_limit_fail_open)
    app.state.onboarding_graph = build_onboarding_graph(provider, DEFAULT_ONBOARDING_POLICY)
    app.state.analysis_graph = build_analysis_graph(provider, DEFAULT_ANALYSIS_POLICY)
    app.state.memory_graph = build_memory_graph(provider, embeddings, DEFAULT_MEMORY_POLICY)
    app.state.conversation_graph = _conversation_graph(
        settings, provider, embeddings, app.state.database_session_factory
    )

    # Added last so it runs first and every response gets a request id.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(api_router)

    return app


def _conversation_graph(
    settings: Settings,
    provider: LLMProvider,
    embeddings: EmbeddingProvider,
    sessions: async_sessionmaker[AsyncSession],
) -> ConversationGraph:
    """Compiled once, like every other graph.

    Retrieval reads through short sessions of its own rather than the request's,
    so no connection is held for the seconds a turn spends waiting on a model.
    Holding one capped concurrent turns at the size of the connection pool:
    measured with a slow fake model, the turns beyond it queued and then failed
    on the pool timeout.
    """
    memories = HybridMemoryRetriever(
        SemanticMemoryRetriever(sessions),
        KeywordMemoryRetriever(sessions),
        embedding_model=settings.embedding_model,
        config=HybridConfig.for_limit(
            settings.memory_retrieval_limit, settings.memory_similarity_threshold
        ),
    )
    return build_conversation_graph(
        provider,
        DEFAULT_CONTEXT_BUDGET,
        embeddings,
        memories,
        TemporalEventRetriever(sessions),
        StructuredActivityQuery(sessions),
        timeline_limit=DEFAULT_CONTEXT_BUDGET.max_timeline_entries,
    )


app = create_app()
