from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.conversation.context import ContextBudget
from app.domain.conversation.verification import (
    DEFAULT_VERIFICATION_POLICY,
    VerificationPolicy,
)
from app.domain.retrieval.grading import (
    DEFAULT_EVIDENCE_POLICY,
    EvidencePolicy,
    next_search_query,
)
from app.graph.conversation.nodes.build_context import build_build_context_node
from app.graph.conversation.nodes.generate_reply import build_generate_reply_node
from app.graph.conversation.nodes.grade_evidence import build_grade_evidence_node
from app.graph.conversation.nodes.retrieve_context import build_retrieve_context_node
from app.graph.conversation.nodes.retry_retrieval import build_retry_retrieval_node
from app.graph.conversation.nodes.understand_query import build_understand_query_node
from app.graph.conversation.nodes.verify_answer import build_verify_answer_node
from app.graph.conversation.state import ConversationNode, ConversationTurnState
from app.infrastructure.llm.embeddings import EmbeddingProvider
from app.infrastructure.llm.provider import LLMProvider
from app.infrastructure.observability.tracing import run_step
from app.retrieval.hybrid import MemoryRetriever
from app.retrieval.structured import StructuredActivityQuery
from app.retrieval.temporal import TemporalEventRetriever

type ConversationGraph = CompiledStateGraph[
    ConversationTurnState, None, ConversationTurnState, ConversationTurnState
]

UNDERSTAND_QUERY = "understand_query"
RETRIEVE_CONTEXT = "retrieve_context"
GRADE_EVIDENCE = "grade_evidence"
RETRY_RETRIEVAL = "retry_retrieval"
BUILD_CONTEXT = "build_context"
GENERATE_REPLY = "generate_reply"
VERIFY_ANSWER = "verify_answer"


def build_route_after_retrieval(
    policy: EvidencePolicy,
) -> Callable[[ConversationTurnState], str]:
    def route_after_retrieval(state: ConversationTurnState) -> str:
        """Grade only when there is something to grade.

        An empty recall is not graded. Hybrid search already matches loosely by
        meaning and by any shared word, so finding nothing at all usually means
        there is nothing to find, most often for a new user. Grading it would
        spend a model call and a retry on every turn they send.
        """
        if policy.enabled and state.get("memories"):
            return GRADE_EVIDENCE
        return BUILD_CONTEXT

    return route_after_retrieval


def build_route_after_grading(
    policy: EvidencePolicy,
) -> Callable[[ConversationTurnState], str]:
    def route_after_grading(state: ConversationTurnState) -> str:
        """Search again only while it can still help and the budget allows.

        The attempt budget is the termination condition. The other exits stop
        earlier when another attempt could not change anything: the evidence is
        already enough, grading failed, or the grader offered no search that
        has not already been run.
        """
        if (state.get("retrieval_attempts") or 0) >= policy.max_retrieval_attempts:
            return BUILD_CONTEXT
        if next_search_query(state.get("evidence_grade"), state.get("searches") or []) is None:
            return BUILD_CONTEXT

        return RETRY_RETRIEVAL

    return route_after_grading


def build_route_after_generation(
    policy: VerificationPolicy,
) -> Callable[[ConversationTurnState], str]:
    def route_after_generation(state: ConversationTurnState) -> str:
        """Verify a new draft; end when there is none to verify."""
        if not policy.enabled or state.get("draft_failed"):
            return END
        return VERIFY_ANSWER

    return route_after_generation


def build_route_after_verification(
    policy: VerificationPolicy,
) -> Callable[[ConversationTurnState], str]:
    def route_after_verification(state: ConversationTurnState) -> str:
        """Regenerate an unsupported reply, but only while the budget allows.

        The attempt budget is the termination condition. A reply that could not
        be verified is not regenerated: with no findings, a retry would be told
        nothing it could act on.
        """
        if state.get("grounded") is not False:
            return END
        if (state.get("generation_attempts") or 0) >= policy.max_generation_attempts:
            return END
        return GENERATE_REPLY

    return route_after_verification


def _traced(name: str, node: ConversationNode) -> ConversationNode:
    """Record the node into the current run's trace, if one is bound.

    Applied here, once, rather than inside each node, so no node can forget it
    and none of them has to know tracing exists.
    """

    async def traced(state: ConversationTurnState) -> ConversationTurnState:
        return await run_step(name, lambda: node(state))

    return traced


def max_steps(evidence_policy: EvidencePolicy, verification_policy: VerificationPolicy) -> int:
    """The most steps a turn can take when both loops spend their whole budget.

    understand_query and retrieve_context, then a grade for every retrieval
    attempt with a retry between each, then build_context, then a generation
    and a verification for every generation attempt.
    """
    retrieval = 2 * evidence_policy.max_retrieval_attempts - 1
    generation = 2 * verification_policy.max_generation_attempts
    return 2 + retrieval + 1 + generation


def build_conversation_graph(
    provider: LLMProvider,
    budget: ContextBudget,
    embeddings: EmbeddingProvider,
    retriever: MemoryRetriever,
    timeline: TemporalEventRetriever | None = None,
    totals: StructuredActivityQuery | None = None,
    *,
    timeline_limit: int = 25,
    evidence_policy: EvidencePolicy = DEFAULT_EVIDENCE_POLICY,
    verification_policy: VerificationPolicy = DEFAULT_VERIFICATION_POLICY,
) -> ConversationGraph:
    """Assemble the conversational workflow.

    Understanding comes first so that gathering is targeted: a question about how
    much the user did last week needs an exact total, and one about how they are
    feeling needs none. Retrieval then runs before the context is built, so that
    everything the model will see competes for one budget in one place.

    Recalled memories are graded before they are used. When they cannot answer
    the question, memory is searched again with a rewritten query and the result
    is graded again.

    Each draft reply is then verified against the context it was given, and an
    unsupported one is regenerated with the statements to avoid. Both cycles
    are bounded by an attempt counter that every pass advances.
    """
    graph = StateGraph(ConversationTurnState)

    def add_node(name: str, node: ConversationNode) -> None:
        graph.add_node(name, _traced(name, node))

    add_node(UNDERSTAND_QUERY, build_understand_query_node(provider))
    add_node(
        RETRIEVE_CONTEXT,
        build_retrieve_context_node(
            embeddings,
            retriever,
            timeline,
            totals,
            timeline_limit=timeline_limit,
        ),
    )
    add_node(GRADE_EVIDENCE, build_grade_evidence_node(provider, evidence_policy))
    add_node(RETRY_RETRIEVAL, build_retry_retrieval_node(embeddings, retriever, evidence_policy))
    add_node(BUILD_CONTEXT, build_build_context_node(budget))
    add_node(GENERATE_REPLY, build_generate_reply_node(provider))
    add_node(VERIFY_ANSWER, build_verify_answer_node(provider))

    graph.add_edge(START, UNDERSTAND_QUERY)
    graph.add_edge(UNDERSTAND_QUERY, RETRIEVE_CONTEXT)
    graph.add_conditional_edges(
        RETRIEVE_CONTEXT,
        build_route_after_retrieval(evidence_policy),
        {GRADE_EVIDENCE: GRADE_EVIDENCE, BUILD_CONTEXT: BUILD_CONTEXT},
    )
    graph.add_conditional_edges(
        GRADE_EVIDENCE,
        build_route_after_grading(evidence_policy),
        {RETRY_RETRIEVAL: RETRY_RETRIEVAL, BUILD_CONTEXT: BUILD_CONTEXT},
    )
    graph.add_edge(RETRY_RETRIEVAL, GRADE_EVIDENCE)
    graph.add_edge(BUILD_CONTEXT, GENERATE_REPLY)
    graph.add_conditional_edges(
        GENERATE_REPLY,
        build_route_after_generation(verification_policy),
        {VERIFY_ANSWER: VERIFY_ANSWER, END: END},
    )
    graph.add_conditional_edges(
        VERIFY_ANSWER,
        build_route_after_verification(verification_policy),
        {GENERATE_REPLY: GENERATE_REPLY, END: END},
    )

    # Set to the exact worst case so a broken loop fails fast (verified in tests).
    return graph.compile().with_config(
        recursion_limit=max_steps(evidence_policy, verification_policy) + 1
    )
