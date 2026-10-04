from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Build the application's connection pool.

    Creating the engine does not open connections, so this is safe to call while
    building the app rather than inside the event loop.
    """
    return create_async_engine(
        str(settings.database_url),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        # Pre-ping avoids stale connections after restarts or idle timeouts.
        pool_pre_ping=True,
        connect_args={
            "timeout": settings.database_connect_timeout_seconds,
            "command_timeout": settings.database_command_timeout_seconds,
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        # Avoid lazy refresh on commit, which can't be awaited.
        expire_on_commit=False,
        autoflush=False,
    )
