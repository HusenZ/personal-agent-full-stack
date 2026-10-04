import logging

from app.domain.memory.types import MemoryExtraction
from app.graph.memory.state import MemoryExtractionState, MemoryNode
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.memory import build_extraction_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def build_extract_candidates_node(provider: LLMProvider) -> MemoryNode:
    async def extract_candidates(state: MemoryExtractionState) -> MemoryExtractionState:
        """Ask what, if anything, this exchange is worth remembering."""
        prompt = build_extraction_prompt(
            state.get("user_message") or "", state.get("assistant_reply") or ""
        )

        try:
            extraction = await provider.generate_structured(prompt, MemoryExtraction)
        except LLMError as error:
            # Losing one exchange's memories is recoverable; the exchange is stored.
            logger.warning("memory_extraction_failed", extra={"error_type": type(error).__name__})
            return {
                "candidates": [],
                "errors": [*(state.get("errors") or []), "extraction_failed"],
            }

        return {"candidates": list(extraction.candidates)}

    return extract_candidates
