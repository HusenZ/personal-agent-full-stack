from typing import Any

from sqlalchemy import Row, Select
from sqlalchemy.ext.asyncio import AsyncSession

# Rows turned into objects between returns to the event loop: about 20 ms of
# work at the measured cost of a message row.
DEFAULT_BATCH_SIZE = 500


async def fetch_all_in_batches[EntityT](
    session: AsyncSession,
    statement: Select[tuple[EntityT]],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[EntityT]:
    """Every entity ``statement`` selects, read through a cursor a batch at a time.

    The result is the same as ``(await session.execute(statement)).scalars().all()``.
    The difference is when the work happens: that call fetches every row in one
    await and then builds every object without yielding, which for a large
    account froze the event loop, and with it every other request on the worker,
    for over a second. Here the loop gets control back between batches.
    """
    result = await session.stream_scalars(statement.execution_options(yield_per=batch_size))
    entities: list[Any] = []
    async for batch in result.partitions():
        entities.extend(batch)
    return entities


async def fetch_rows_in_batches[RowT: tuple[Any, ...]](
    session: AsyncSession, statement: Select[RowT], batch_size: int = DEFAULT_BATCH_SIZE
) -> list[Row[RowT]]:
    """As :func:`fetch_all_in_batches`, for a statement selecting columns.

    Plain rows rather than entities for the largest reads: measured on 36,000
    messages they load 2.4 times faster and leave the garbage collector less
    than half as many objects to track, whose full collections otherwise pause
    the whole process for hundreds of milliseconds.
    """
    result = await session.stream(statement.execution_options(yield_per=batch_size))
    rows: list[Row[RowT]] = []
    async for batch in result.partitions():
        rows.extend(batch)
    return rows
