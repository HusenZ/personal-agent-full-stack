from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.analysis.policy import AnalysisPolicy
from app.graph.analysis.nodes.build_evidence import build_evidence
from app.graph.analysis.nodes.generate_analysis import build_generate_analysis_node
from app.graph.analysis.nodes.verify_grounding import build_verify_grounding_node
from app.graph.analysis.state import AnalysisNode, AnalysisState
from app.infrastructure.llm.provider import LLMProvider
from app.infrastructure.observability.tracing import run_step

type AnalysisGraph = CompiledStateGraph[AnalysisState, None, AnalysisState, AnalysisState]

BUILD_EVIDENCE = "build_evidence"
GENERATE_ANALYSIS = "generate_analysis"
VERIFY_GROUNDING = "verify_grounding"


def build_route_after_verification(policy: AnalysisPolicy) -> Callable[[AnalysisState], str]:
    def route_after_verification(state: AnalysisState) -> str:
        """Retry a thin analysis, but only while the attempt budget allows.

        The attempt counter is the termination condition; without it a model that
        keeps citing evidence that does not exist would loop at unbounded cost.
        """
        accepted = state.get("claims_accepted_best") or ()
        attempt = state.get("attempt") or 0

        if len(accepted) >= policy.min_claims:
            return END
        if attempt >= policy.max_generation_attempts:
            return END

        return GENERATE_ANALYSIS

    return route_after_verification


def _traced(name: str, node: AnalysisNode) -> AnalysisNode:
    """Record the node into the current run's trace, if one is bound.

    Applied here, once, rather than inside each node, so no node can forget it
    and none of them has to know tracing exists.
    """

    async def traced(state: AnalysisState) -> AnalysisState:
        return await run_step(name, lambda: node(state))

    return traced


def max_steps(policy: AnalysisPolicy) -> int:
    """The most steps a run can take when the retry loop spends its whole budget.

    build_evidence, then a generation and a verification for every attempt.
    """
    return 1 + 2 * policy.max_generation_attempts


def build_analysis_graph(provider: LLMProvider, policy: AnalysisPolicy) -> AnalysisGraph:
    graph = StateGraph(AnalysisState)

    def add_node(name: str, node: AnalysisNode) -> None:
        graph.add_node(name, _traced(name, node))

    add_node(BUILD_EVIDENCE, build_evidence)
    add_node(GENERATE_ANALYSIS, build_generate_analysis_node(provider))
    add_node(VERIFY_GROUNDING, build_verify_grounding_node(policy))

    graph.add_edge(START, BUILD_EVIDENCE)
    graph.add_edge(BUILD_EVIDENCE, GENERATE_ANALYSIS)
    graph.add_edge(GENERATE_ANALYSIS, VERIFY_GROUNDING)
    graph.add_conditional_edges(
        VERIFY_GROUNDING,
        build_route_after_verification(policy),
        {GENERATE_ANALYSIS: GENERATE_ANALYSIS, END: END},
    )

    # Set to the exact worst case so a broken loop fails fast, and so a trace
    # can never hold more steps than the policy allows (verified in tests).
    return graph.compile().with_config(recursion_limit=max_steps(policy) + 1)
