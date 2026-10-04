from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.onboarding.policy import OnboardingPolicy
from app.graph.onboarding.nodes.assess_context import build_assess_context_node
from app.graph.onboarding.nodes.build_profile import build_profile
from app.graph.onboarding.nodes.extract_information import build_extract_information_node
from app.graph.onboarding.nodes.generate_question import build_generate_question_node
from app.graph.onboarding.state import OnboardingTurnState
from app.infrastructure.llm.provider import LLMProvider

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


def build_onboarding_graph(provider: LLMProvider, policy: OnboardingPolicy) -> OnboardingGraph:
    graph = StateGraph(OnboardingTurnState)

    graph.add_node(EXTRACT_INFORMATION, build_extract_information_node(provider))
    graph.add_node(ASSESS_CONTEXT, build_assess_context_node(policy))
    graph.add_node(GENERATE_QUESTION, build_generate_question_node(provider))
    graph.add_node(BUILD_PROFILE, build_profile)

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
