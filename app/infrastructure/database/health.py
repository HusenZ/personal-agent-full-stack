import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def check_database_connection(engine: AsyncEngine, timeout_seconds: float) -> bool:
    """Report whether the database can currently serve a trivial query.

    Returns a boolean instead of raising because a readiness probe must always
    answer; the failure is logged so it stays diagnosable.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        logger.warning("database_health_check_failed", exc_info=True)
        return False

    return True
