from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.memory.policy import MemoryPolicy
from app.graph.memory.nodes.embed_candidates import build_embed_candidates_node
from app.graph.memory.nodes.extract_candidates import build_extract_candidates_node
from app.graph.memory.nodes.filter_candidates import build_filter_candidates_node
from app.graph.memory.nodes.resolve_conflicts import build_resolve_conflicts_node
from app.graph.memory.state import MemoryExtractionState
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.provider import LLMProvider

type MemoryGraph = CompiledStateGraph[
    MemoryExtractionState, None, MemoryExtractionState, MemoryExtractionState
]

EXTRACT_CANDIDATES = "extract_candidates"
EMBED_CANDIDATES = "embed_candidates"
FILTER_CANDIDATES = "filter_candidates"
RESOLVE_CONFLICTS = "resolve_conflicts"


def has_survivors(state: MemoryExtractionState) -> str:
    """Skip embedding and comparison when nothing survived the filter.

    The common case for an ordinary exchange is that nothing is worth keeping,
    and that case should cost one model call rather than three.
    """
    return EMBED_CANDIDATES if state.get("admitted") else END


def build_memory_graph(
    provider: LLMProvider, embeddings: EmbeddingProvider, policy: MemoryPolicy
) -> MemoryGraph:
    graph = StateGraph(MemoryExtractionState)

    graph.add_node(EXTRACT_CANDIDATES, build_extract_candidates_node(provider))
    graph.add_node(FILTER_CANDIDATES, build_filter_candidates_node(policy))
    graph.add_node(EMBED_CANDIDATES, build_embed_candidates_node(embeddings))
    graph.add_node(RESOLVE_CONFLICTS, build_resolve_conflicts_node(provider, policy))

    graph.add_edge(START, EXTRACT_CANDIDATES)
    graph.add_edge(EXTRACT_CANDIDATES, FILTER_CANDIDATES)
    graph.add_conditional_edges(
        FILTER_CANDIDATES, has_survivors, {EMBED_CANDIDATES: EMBED_CANDIDATES, END: END}
    )
    graph.add_edge(EMBED_CANDIDATES, RESOLVE_CONFLICTS)
    graph.add_edge(RESOLVE_CONFLICTS, END)

    return graph.compile()
