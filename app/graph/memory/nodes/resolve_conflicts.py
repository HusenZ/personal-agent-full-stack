import logging

from app.domain.memory.decisions import MemoryJudgment, decide
from app.domain.memory.pairing import CandidatePairing, pair_candidate
from app.domain.memory.policy import MemoryPolicy
from app.graph.memory.state import MemoryExtractionState, MemoryNode
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.prompts.memory import build_judgement_prompt
from app.infrastructure.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def build_resolve_conflicts_node(provider: LLMProvider, policy: MemoryPolicy) -> MemoryNode:
    async def resolve_conflicts(state: MemoryExtractionState) -> MemoryExtractionState:
        """Work out, for each surviving candidate, what it does to what is held.

        The expensive step is narrowed before it runs. Pairing picks out the few
        existing memories a candidate could plausibly duplicate or contradict, and
        settles outright restatements itself; only genuine ambiguity is worth
        asking a model about. Without that, every extraction would mean showing a
        model the user's whole history.
        """
        existing = list(state.get("existing") or [])
        admitted = state.get("admitted") or []
        embeddings = state.get("embeddings") or [None] * len(admitted)
        decisions = []
        judged_pairs = 0

        for candidate, embedding in zip(admitted, embeddings, strict=False):
            pairing = pair_candidate(candidate, existing, policy, candidate_embedding=embedding)

            judgment: MemoryJudgment | None = None
            if pairing.to_judge:
                judged_pairs += len(pairing.to_judge)
                judgment = await _judge(provider, pairing)

            decisions.append(decide(pairing, judgment, embedding=embedding))

        return {"decisions": decisions, "judged_pairs": judged_pairs}

    return resolve_conflicts


async def _judge(provider: LLMProvider, pairing: CandidatePairing) -> MemoryJudgment | None:
    """Ask for verdicts on one candidate, or give up on that candidate alone.

    Without a verdict the candidate is simply created. Recording a new memory
    alongside an old one is a smaller mistake than superseding something on the
    strength of a call that failed.
    """
    try:
        return await provider.generate_structured(
            build_judgement_prompt(pairing.candidate, pairing.to_judge), MemoryJudgment
        )
    except LLMError as error:
        logger.warning("memory_judgement_failed", extra={"error_type": type(error).__name__})
        return None
