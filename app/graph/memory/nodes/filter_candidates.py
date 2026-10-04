import logging

from app.domain.memory.decisions import admit
from app.domain.memory.policy import MemoryPolicy
from app.graph.memory.state import MemoryExtractionState, MemoryNode

logger = logging.getLogger(__name__)


def build_filter_candidates_node(policy: MemoryPolicy) -> MemoryNode:
    async def filter_candidates(state: MemoryExtractionState) -> MemoryExtractionState:
        """Drop what is not worth keeping, before anything else costs money.

        Deterministic and ahead of the comparison step on purpose: a candidate
        that fails the bar should never reach a second model call.
        """
        candidates = state.get("candidates") or []
        admitted = [candidate for candidate in candidates if admit(candidate, policy)]

        rejected = len(candidates) - len(admitted)
        if rejected:
            logger.info(
                "memory_candidates_rejected",
                extra={"proposed": len(candidates), "rejected": rejected},
            )

        return {"admitted": admitted, "rejected_count": rejected}

    return filter_candidates
