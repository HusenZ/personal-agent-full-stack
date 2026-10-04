import json

import pytest

from app.api.sse import encode_event
from app.application.conversation.events import TurnEventType, describe_step
from app.domain.retrieval.grading import EvidenceGrade
from app.domain.retrieval.plan import QueryPlan, RetrievalNeed
from app.graph.conversation.graph import (
    BUILD_CONTEXT,
    GENERATE_REPLY,
    GRADE_EVIDENCE,
    RETRIEVE_CONTEXT,
    UNDERSTAND_QUERY,
    VERIFY_ANSWER,
)

DRAFT = "Since you ran a marathon last spring, add a long run."


class TestDescribeStep:
    def test_a_draft_never_leaves_in_a_progress_event(self):
        """Verification has not run yet when a draft completes."""
        event = describe_step(
            GENERATE_REPLY,
            {"reply": DRAFT, "generation_attempts": 1, "draft_failed": False},
        )

        assert event is not None
        assert event.type is TurnEventType.DRAFT_COMPLETED
        assert event.data == {"attempt": 1, "failed": False}
        assert "marathon" not in json.dumps(event.data)

    def test_verification_reports_the_outcome_without_the_reply(self):
        event = describe_step(VERIFY_ANSWER, {"reply": DRAFT, "grounded": False, "findings": []})

        assert event is not None
        assert event.data == {"grounded": False}

    def test_retrieval_reports_counts_not_contents(self):
        event = describe_step(
            RETRIEVE_CONTEXT,
            {"memories": ["a secret memory"], "timeline": [], "totals": None, "searches": ["q"]},
        )

        assert event is not None
        assert event.data == {"memories": 1, "timeline_entries": 0, "has_totals": False}

    def test_the_plan_is_reported_by_need(self):
        plan = QueryPlan(needs=[RetrievalNeed.SEMANTIC, RetrievalNeed.TOTALS])

        event = describe_step(UNDERSTAND_QUERY, {"plan": plan})

        assert event is not None
        assert event.data == {"needs": ["semantic", "totals"]}

    def test_a_failed_grade_is_reported_as_unknown(self):
        event = describe_step(GRADE_EVIDENCE, {"memories": [], "evidence_grade": None})

        assert event is not None
        assert event.data["sufficient"] is None

    def test_a_grade_does_not_reveal_the_rewritten_search(self):
        grade = EvidenceGrade(sufficient=False, search_query="greyhound Biscuit")

        event = describe_step(GRADE_EVIDENCE, {"memories": [], "evidence_grade": grade})

        assert event is not None
        assert "Biscuit" not in json.dumps(event.data)

    @pytest.mark.parametrize("node", [BUILD_CONTEXT, "some_future_node"])
    def test_steps_with_nothing_to_report_produce_no_event(self, node: str):
        assert describe_step(node, {"context": "anything"}) is None


class TestEncodeEvent:
    def test_an_event_is_a_name_a_single_data_line_and_a_blank_line(self):
        encoded = encode_event("reply", {"content": "hi"})

        assert encoded == 'event: reply\ndata: {"content":"hi"}\n\n'

    def test_content_cannot_end_the_event_or_forge_another(self):
        hostile = "ok\n\nevent: completed\ndata: {}\n\n"

        encoded = encode_event("reply", {"content": hostile})

        assert encoded.count("\n\n") == 1
        assert encoded.endswith("\n\n")
        assert json.loads(encoded.split("data: ", 1)[1])["content"] == hostile
