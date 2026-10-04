import logging

from app.domain.analysis.claims import AnalysisResult
from app.domain.analysis.evidence import EvidenceBundle
from app.graph.analysis.state import AnalysisNode, AnalysisState
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.analysis import build_analysis_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def build_generate_analysis_node(provider: LLMProvider) -> AnalysisNode:
    async def generate_analysis(state: AnalysisState) -> AnalysisState:
        evidence = state.get("evidence") or EvidenceBundle(items=())
        attempt = (state.get("attempt") or 0) + 1

        prompt = build_analysis_prompt(evidence, feedback=state.get("rejection_feedback"))

        try:
            result = await provider.generate_structured(prompt, AnalysisResult)
        except LLMError as error:
            # Let verification decide on retries, keeping the budget in one place.
            logger.warning(
                "analysis_generation_failed",
                extra={"error_type": type(error).__name__, "attempt": attempt},
            )
            return {
                "attempt": attempt,
                "claims": [],
                "errors": [*(state.get("errors") or []), "generation_failed"],
            }

        return {"attempt": attempt, "claims": list(result.claims)}

    return generate_analysis
