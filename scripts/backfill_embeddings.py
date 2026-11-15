"""Embed memories semantic search cannot use: no vector, or one from another model.

    uv run python -m scripts.backfill_embeddings --dry-run           # count only
    uv run python -m scripts.backfill_embeddings                     # embed them
    uv run python -m scripts.backfill_embeddings --batch-size 100
    uv run python -m scripts.backfill_embeddings --include-inactive  # archived and superseded too

Reads and writes the database in DATABASE_URL and embeds with EMBEDDING_MODEL.
Run it after changing EMBEDDING_MODEL, after an embedding outage, or on a
schedule: a run with nothing to do makes no provider calls. Exits 1 if the
provider failed on any memory; those are left as they were, and the next run
tries them again. Prints counts only; nothing a user wrote is printed.
"""

import argparse
import asyncio

from app.application.memory.backfill import DEFAULT_BATCH_SIZE, BackfillReport, EmbeddingBackfill
from app.core.config import get_settings
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.infrastructure.llm.embeddings import OpenAIEmbeddingProvider
from app.infrastructure.llm.exceptions import LLMConfigurationError
from app.infrastructure.observability.logging import configure_logging


async def backfill(batch_size: int, include_inactive: bool, dry_run: bool) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    engine = create_database_engine(settings)
    try:
        job = EmbeddingBackfill(
            create_session_factory(engine),
            OpenAIEmbeddingProvider(settings),
            settings.embedding_model,
            batch_size=batch_size,
            include_inactive=include_inactive,
        )
        if dry_run:
            pending = await job.pending()
            print(f"{pending} memories need embedding with {settings.embedding_model}")
            return 0
        try:
            report = await job.run()
        except LLMConfigurationError as error:
            raise SystemExit(f"embedding provider unusable: {error}") from error
    finally:
        await engine.dispose()

    print(_summary(report, settings.embedding_model))
    return 1 if report.failed else 0


def _summary(report: BackfillReport, embedding_model: str) -> str:
    return (
        f"{embedding_model}: {report.embedded} embedded, {report.failed} failed, "
        f"{report.changed} changed during the run, in {report.batches} batches"
    )


def _batch_size(value: str) -> int:
    size = int(value)
    if not 1 <= size <= 1000:
        raise argparse.ArgumentTypeError("must be between 1 and 1000")
    return size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--batch-size",
        type=_batch_size,
        default=DEFAULT_BATCH_SIZE,
        help=f"memories per provider call and per transaction (default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="also embed archived and superseded memories, which search never returns",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="count what needs embedding; call nothing"
    )
    arguments = parser.parse_args()
    raise SystemExit(
        asyncio.run(backfill(arguments.batch_size, arguments.include_inactive, arguments.dry_run))
    )


if __name__ == "__main__":
    main()
