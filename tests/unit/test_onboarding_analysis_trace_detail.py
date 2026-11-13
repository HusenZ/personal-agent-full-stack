"""What onboarding and analysis traces may keep per step: counts and outcomes only."""

import json

import pytest

from app.application.analysis.steps import describe_analysis_step
from app.application.onboarding.steps import describe_onboarding_step
from app.domain.analysis.claims import AnalysisClaim, AnalysisSection, ClaimType
from app.domain.analysis.evidence import EvidenceBundle, EvidenceItem, EvidenceKind
from app.domain.analysis.grounding import RejectedClaim, RejectionReason
from app.domain.onboarding.context import CollectedContext, ExtractedGoal
from app.domain.onboarding.dimensions import ContextDimension
from app.graph.onboarding.state import AskedQuestion, ProfileDraft

PRIVATE = "I am leaving my marriage and moving to Pune"


def claim(claim_type: ClaimType = ClaimType.FACT) -> AnalysisClaim:
    return AnalysisClaim(
        section=AnalysisSection.GOALS,
        claim_type=claim_type,
        content=PRIVATE,
        confidence=0.9,
        evidence_ids=["goal:1"],
    )


class TestOnboardingDetail:
    def test_extraction_reports_usability_not_content(self):
        update = {
            "answer_was_usable": True,
            "collected": CollectedContext(goals=[ExtractedGoal(name=PRIVATE, confidence=0.9)]),
        }

        detail = describe_onboarding_step("extract_information", update)

        assert detail == {"answer_usable": True, "extraction_failed": False}

    def test_a_failed_extraction_is_reported(self):
        update = {"answer_was_usable": False, "errors": ["extraction_failed"]}

        detail = describe_onboarding_step("extract_information", update)

        assert detail == {"answer_usable": False, "extraction_failed": True}

    def test_assessment_reports_scores_and_the_decision(self):
        update = {
            "coverage": {"goal": 0.912345, "context": 0.5},
            "overall_confidence": 0.712345,
            "is_complete": False,
            "completion_reason": "confidence_below_threshold",
        }

        detail = describe_onboarding_step("assess_context", update)

        assert detail == {
            "overall_confidence": 0.7123,
            "coverage": {"goal": 0.9123, "context": 0.5},
            "is_complete": False,
            "completion_reason": "confidence_below_threshold",
        }

    def test_a_question_is_reported_by_dimension_not_text(self):
        question = AskedQuestion(dimension=ContextDimension.CONSTRAINT, text=PRIVATE)
        update = {"next_question": question, "asked_questions": [question], "questions_asked": 3}

        detail = describe_onboarding_step("generate_question", update)

        assert detail == {"dimension": "constraint", "questions_asked": 3}

    def test_the_profile_draft_is_counted_not_copied(self):
        draft = ProfileDraft(
            goals=[(PRIVATE, "high")], constraints=[PRIVATE, PRIVATE], preferences={"k": PRIVATE}
        )

        detail = describe_onboarding_step("build_profile", {"profile_draft": draft})

        assert detail == {"goals": 1, "constraints": 2, "preferences": 1}


class TestAnalysisDetail:
    def test_evidence_is_counted_not_copied(self):
        evidence = EvidenceBundle(
            items=(EvidenceItem(id="goal:1", kind=EvidenceKind.GOAL, content=PRIVATE),)
        )

        assert describe_analysis_step("build_evidence", {"evidence": evidence}) == {
            "evidence_items": 1
        }

    def test_generation_reports_attempt_and_claim_count(self):
        detail = describe_analysis_step("generate_analysis", {"attempt": 2, "claims": [claim()]})

        assert detail == {"attempt": 2, "claims": 1, "failed": False}

    def test_a_failed_generation_is_reported(self):
        update = {"attempt": 1, "claims": [], "errors": ["generation_failed"]}

        detail = describe_analysis_step("generate_analysis", update)

        assert detail == {"attempt": 1, "claims": 0, "failed": True}

    def test_verification_reports_kept_types_and_rejection_reasons(self):
        kept = (claim(), claim(ClaimType.INFERENCE), claim())
        update = {
            "claims_accepted_best": kept,
            "claims": list(kept),
            "rejected": [
                RejectedClaim(claim=claim(), reason=RejectionReason.UNKNOWN_EVIDENCE),
                RejectedClaim(claim=claim(), reason=RejectionReason.UNKNOWN_EVIDENCE),
                RejectedClaim(claim=claim(), reason=RejectionReason.UNDERCONFIDENT_FACT),
            ],
            "rejection_feedback": PRIVATE,
            "overall_confidence": 0.83333,
            "degraded": False,
        }

        detail = describe_analysis_step("verify_grounding", update)

        assert detail == {
            "claims_kept": 3,
            "claim_types": {"fact": 2, "inference": 1},
            "rejected": {"underconfident_fact": 1, "unknown_evidence": 2},
            "overall_confidence": 0.8333,
            "degraded": False,
        }


class TestNothingPersonalLeaks:
    @pytest.mark.parametrize(
        ("describe", "node", "update"),
        [
            (
                describe_onboarding_step,
                "generate_question",
                {"next_question": AskedQuestion(dimension=ContextDimension.GOAL, text=PRIVATE)},
            ),
            (
                describe_onboarding_step,
                "build_profile",
                {"profile_draft": ProfileDraft(constraints=[PRIVATE])},
            ),
            (describe_analysis_step, "generate_analysis", {"attempt": 1, "claims": [claim()]}),
            (
                describe_analysis_step,
                "verify_grounding",
                {"claims_accepted_best": (claim(),), "rejection_feedback": PRIVATE},
            ),
        ],
    )
    def test_no_detail_carries_what_the_user_said_or_the_model_wrote(self, describe, node, update):
        assert PRIVATE not in json.dumps(describe(node, update))

    @pytest.mark.parametrize("describe", [describe_onboarding_step, describe_analysis_step])
    def test_an_unknown_step_keeps_nothing(self, describe):
        assert describe("understand_query", {"answer": PRIVATE}) is None
