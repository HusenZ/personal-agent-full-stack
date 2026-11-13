from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.onboarding.policy import OnboardingPolicy
from app.graph.onboarding.nodes.assess_context import build_assess_context_node
from app.graph.onboarding.nodes.build_profile import build_profile
from app.graph.onboarding.nodes.extract_information import build_extract_information_node
from app.graph.onboarding.nodes.generate_question import build_generate_question_node
from app.graph.onboarding.state import OnboardingNode, OnboardingTurnState
from app.infrastructure.llm.provider import LLMProvider
from app.infrastructure.observability.tracing import run_step

type OnboardingGraph = CompiledStateGraph[
    OnboardingTurnState, None, OnboardingTurnState, OnboardingTurnState
]

EXTRACT_INFORMATION = "extract_information"
ASSESS_CONTEXT = "assess_context"
GENERATE_QUESTION = "generate_question"
BUILD_PROFILE = "build_profile"


def route_after_assessment(state: OnboardingTurnState) -> str:
    """Either the interview has learned enough, or it asks one more question.

    The loop this closes lives across HTTP turns rather than inside a single
    graph run, so the graph itself has no cycle and cannot spin.
    """
    return BUILD_PROFILE if state.get("is_complete") else GENERATE_QUESTION


def _traced(name: str, node: OnboardingNode) -> OnboardingNode:
    """Record the node into the current run's trace, if one is bound.

    Applied here, once, rather than inside each node, so no node can forget it
    and none of them has to know tracing exists.
    """

    async def traced(state: OnboardingTurnState) -> OnboardingTurnState:
        return await run_step(name, lambda: node(state))

    return traced


def build_onboarding_graph(provider: LLMProvider, policy: OnboardingPolicy) -> OnboardingGraph:
    graph = StateGraph(OnboardingTurnState)

    def add_node(name: str, node: OnboardingNode) -> None:
        graph.add_node(name, _traced(name, node))

    add_node(EXTRACT_INFORMATION, build_extract_information_node(provider))
    add_node(ASSESS_CONTEXT, build_assess_context_node(policy))
    add_node(GENERATE_QUESTION, build_generate_question_node(provider))
    add_node(BUILD_PROFILE, build_profile)

    graph.add_edge(START, EXTRACT_INFORMATION)
    graph.add_edge(EXTRACT_INFORMATION, ASSESS_CONTEXT)
    graph.add_conditional_edges(
        ASSESS_CONTEXT,
        route_after_assessment,
        {GENERATE_QUESTION: GENERATE_QUESTION, BUILD_PROFILE: BUILD_PROFILE},
    )
    graph.add_edge(GENERATE_QUESTION, END)
    graph.add_edge(BUILD_PROFILE, END)

    return graph.compile()
