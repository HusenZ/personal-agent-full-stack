from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.analysis.service import AnalysisService
from app.application.auth.exceptions import InactiveUserError
from app.application.auth.service import AuthService
from app.application.conversation.service import ConversationService, ExtractionSettings
from app.application.memory.service import MemoryService
from app.application.onboarding.service import OnboardingService
from app.application.planning.service import PlanningService
from app.application.profile.service import ProfileService
from app.application.runs.service import RunService
from app.application.subscription.service import SubscriptionService
from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.security import InvalidTokenError, decode_access_token
from app.domain.conversation.context import DEFAULT_CONTEXT_BUDGET
from app.domain.memory.policy import DEFAULT_MEMORY_POLICY
from app.graph.analysis.graph import AnalysisGraph
from app.graph.conversation.graph import ConversationGraph
from app.graph.memory.graph import MemoryGraph
from app.graph.onboarding.graph import OnboardingGraph
from app.infrastructure.cache.rate_limiter import RateLimiter
from app.infrastructure.database.models import User
from app.infrastructure.database.repositories.analysis import AnalysisRepository
from app.infrastructure.database.repositories.conversation import ConversationRepository
from app.infrastructure.database.repositories.event import EventRepository
from app.infrastructure.database.repositories.job import JobRepository
from app.infrastructure.database.repositories.memory import MemoryRepository
from app.infrastructure.database.repositories.onboarding import OnboardingSessionRepository
from app.infrastructure.database.repositories.plan import WeekPlanRepository
from app.infrastructure.database.repositories.profile import ProfileRepository
from app.infrastructure.database.repositories.refresh_token import RefreshTokenRepository
from app.infrastructure.database.repositories.run import RunRepository
from app.infrastructure.database.repositories.subscription import SubscriptionRepository
from app.infrastructure.database.repositories.user import UserRepository
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.provider import LLMProvider


def get_app_settings(request: Request) -> Settings:
    """Resolve the settings the application was constructed with.

    Reading from application state rather than re-reading the environment keeps a
    single source of configuration per running app, including in tests.
    """
    return cast(Settings, request.app.state.settings)


def get_database_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.database_engine)


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide a request-scoped session.

    The session is deliberately not committed here: transaction boundaries belong
    to the application services that know what constitutes a complete use case.
    Leaving the context manager rolls back anything uncommitted.
    """
    session_factory = cast(
        async_sessionmaker[AsyncSession], request.app.state.database_session_factory
    )
    async with session_factory() as session:
        yield session


def get_redis_client(request: Request) -> Redis | None:
    return cast("Redis | None", request.app.state.redis_client)


def get_rate_limiter(request: Request) -> RateLimiter:
    return cast(RateLimiter, request.app.state.rate_limiter)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
RedisClientDep = Annotated["Redis | None", Depends(get_redis_client)]
DatabaseEngineDep = Annotated[AsyncEngine, Depends(get_database_engine)]
SessionDep = Annotated[AsyncSession, Depends(get_database_session)]


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(
        session=session,
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        profiles=ProfileRepository(session),
        settings=settings,
    )


def get_profile_service(session: SessionDep) -> ProfileService:
    return ProfileService(session=session, profiles=ProfileRepository(session))


def get_onboarding_graph(request: Request) -> OnboardingGraph:
    """The compiled interview graph, built once when the app was created.

    Compiling per request would repeat the same work on every turn for a graph
    whose shape never changes.
    """
    return cast(OnboardingGraph, request.app.state.onboarding_graph)


def get_onboarding_service(
    session: SessionDep, graph: Annotated[OnboardingGraph, Depends(get_onboarding_graph)]
) -> OnboardingService:
    return OnboardingService(
        session=session,
        sessions=OnboardingSessionRepository(session),
        profiles=ProfileService(session=session, profiles=ProfileRepository(session)),
        graph=graph,
    )


def get_analysis_graph(request: Request) -> AnalysisGraph:
    return cast(AnalysisGraph, request.app.state.analysis_graph)


def get_analysis_service(
    session: SessionDep, graph: Annotated[AnalysisGraph, Depends(get_analysis_graph)]
) -> AnalysisService:
    return AnalysisService(
        session=session,
        analyses=AnalysisRepository(session),
        profiles=ProfileRepository(session),
        onboarding_sessions=OnboardingSessionRepository(session),
        graph=graph,
    )


def get_llm_provider(request: Request) -> LLMProvider:
    return cast(LLMProvider, request.app.state.llm_provider)


def get_planning_service(
    session: SessionDep, llm: Annotated[LLMProvider, Depends(get_llm_provider)]
) -> PlanningService:
    return PlanningService(
        session=session,
        plans=WeekPlanRepository(session),
        profiles=ProfileRepository(session),
        onboarding_sessions=OnboardingSessionRepository(session),
        llm=llm,
    )


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    return cast(EmbeddingProvider, request.app.state.embedding_provider)


EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]


def get_conversation_graph(request: Request) -> ConversationGraph:
    return cast(ConversationGraph, request.app.state.conversation_graph)


def get_conversation_service(
    session: SessionDep,
    settings: SettingsDep,
    graph: Annotated[ConversationGraph, Depends(get_conversation_graph)],
) -> ConversationService:
    return ConversationService(
        session=session,
        conversations=ConversationRepository(session),
        profiles=ProfileRepository(session),
        runs=RunRepository(session),
        jobs=JobRepository(session),
        graph=graph,
        budget=DEFAULT_CONTEXT_BUDGET,
        extraction=ExtractionSettings(
            enabled=settings.memory_extraction_enabled, max_attempts=settings.job_max_attempts
        ),
    )


def get_memory_graph(request: Request) -> MemoryGraph:
    return cast(MemoryGraph, request.app.state.memory_graph)


def get_memory_service(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    graph: Annotated[MemoryGraph, Depends(get_memory_graph)],
) -> MemoryService:
    return MemoryService(
        session=session,
        memories=MemoryRepository(session),
        events=EventRepository(session),
        graph=graph,
        policy=DEFAULT_MEMORY_POLICY,
        embeddings=get_embedding_provider(request),
        embedding_model=settings.embedding_model,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
MemoryServiceDep = Annotated[MemoryService, Depends(get_memory_service)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]
PlanningServiceDep = Annotated[PlanningService, Depends(get_planning_service)]
ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]
OnboardingServiceDep = Annotated[OnboardingService, Depends(get_onboarding_service)]

# auto_error=False keeps a missing header in our own error envelope.
_bearer_scheme = HTTPBearer(auto_error=False, description="Access token")
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


async def get_current_user(
    credentials: BearerCredentials, session: SessionDep, settings: SettingsDep
) -> User:
    """Resolve the authenticated user from the bearer token.

    The user is loaded on every request rather than trusted from the token alone,
    so deactivating an account takes effect immediately instead of at token expiry.
    """
    if credentials is None:
        raise AuthenticationError("Authentication credentials were not provided.")

    try:
        claims = decode_access_token(
            credentials.credentials, settings.jwt_secret, settings.jwt_algorithm
        )
    except InvalidTokenError as error:
        raise AuthenticationError("Access token is invalid or expired.") from error

    user = await UserRepository(session).get_by_id(claims.user_id)
    if user is None:
        # The token verified, so this is a deleted account rather than a forgery.
        raise AuthenticationError("Access token is invalid or expired.")

    if not user.is_active:
        raise InactiveUserError()

    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_run_service(session: SessionDep) -> RunService:
    return RunService(RunRepository(session))


RunServiceDep = Annotated[RunService, Depends(get_run_service)]


def get_subscription_service(session: SessionDep) -> SubscriptionService:
    return SubscriptionService(session=session, subscriptions=SubscriptionRepository(session))


SubscriptionServiceDep = Annotated[SubscriptionService, Depends(get_subscription_service)]
