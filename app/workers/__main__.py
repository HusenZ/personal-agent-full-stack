"""Run the background worker: uv run python -m app.workers"""

import asyncio
import signal

from app.core.config import get_settings
from app.domain.memory.policy import DEFAULT_MEMORY_POLICY
from app.graph.memory.graph import build_memory_graph
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.infrastructure.llm.embeddings import OpenAIEmbeddingProvider
from app.infrastructure.llm.openai_provider import OpenAIStructuredProvider
from app.infrastructure.llm.tracing import TracingEmbeddingProvider, TracingLLMProvider
from app.infrastructure.observability.logging import configure_logging
from app.workers.runner import build_worker


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    engine = create_database_engine(settings)
    embeddings = TracingEmbeddingProvider(OpenAIEmbeddingProvider(settings))
    graph = build_memory_graph(
        TracingLLMProvider(OpenAIStructuredProvider(settings)), embeddings, DEFAULT_MEMORY_POLICY
    )
    worker = build_worker(settings, create_session_factory(engine), graph, embeddings)

    # On a stop signal, let the current job finish within its timeout.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop.set)

    try:
        await worker.run(stop)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
