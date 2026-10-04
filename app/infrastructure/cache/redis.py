import asyncio
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings

logger = logging.getLogger(__name__)


def create_redis_client(settings: Settings) -> Redis | None:
    """Build the Redis client, or None when no Redis is configured.

    Connections are established lazily, so this is safe to call while building
    the application rather than inside the event loop.
    """
    if settings.redis_url is None:
        return None

    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_timeout=settings.rate_limit_timeout_seconds,
        socket_connect_timeout=settings.rate_limit_timeout_seconds,
        health_check_interval=30,
    )


async def check_redis_connection(client: Redis | None, timeout_seconds: float) -> bool:
    """Report whether Redis can currently answer.

    Returns a boolean rather than raising because a readiness probe must always
    answer; the failure is logged so it stays diagnosable.
    """
    if client is None:
        return False

    try:
        async with asyncio.timeout(timeout_seconds):
            await client.ping()
    except (RedisError, OSError, TimeoutError):
        logger.warning("redis_health_check_failed", exc_info=True)
        return False

    return True
