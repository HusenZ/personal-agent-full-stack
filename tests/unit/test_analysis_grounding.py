import pytest

from app.domain.analysis.claims import AnalysisClaim, AnalysisSection, ClaimType
from app.domain.analysis.evidence import (
    EvidenceKind,
    RawEvidence,
    build_evidence_bundle,
)
from app.domain.analysis.grounding import (
    MIN_CONFIDENCE_FOR_FACT,
    RejectionReason,
    check_claim,
    ground_claims,
    overall_confidence,
)
from app.domain.analysis.policy import AnalysisPolicy

EVIDENCE = build_evidence_bundle(
    RawEvidence(
        goals=("Move into AI engineering",),
        constraints=("Only evenings free",),
        preferences=(("communication_style", "direct"),),
        observations=("Flutter developer today",),
    )
)


def claim(
    claim_type: ClaimType | str = ClaimType.FACT,
    *,
    confidence: float = 0.9,
    evidence_ids: list[str] | None = None,
    content: str = "A statement about the user",
) -> AnalysisClaim:
    return AnalysisClaim(
        section=AnalysisSection.GOALS,
        claim_type=ClaimType(claim_type),
        content=content,
        confidence=confidence,
        evidence_ids=evidence_ids if evidence_ids is not None else ["goal:1"],
    )


class TestEvidenceBundle:
    def test_every_item_is_addressable(self):
        assert EVIDENCE.ids == {
            "goal:1",
            "constraint:1",
            "preference:communication_style",
            "observation:1",
        }

    def test_each_item_keeps_its_kind(self):
        kinds = {item.id: item.kind for item in EVIDENCE.items}

        assert kinds["goal:1"] is EvidenceKind.GOAL
        assert kinds["observation:1"] is EvidenceKind.OBSERVATION

    def test_rendering_exposes_the_citable_id(self):
        rendered = EVIDENCE.render()

        assert "[goal:1]" in rendered
        assert "Move into AI engineering" in rendered

    def test_an_empty_profile_produces_an_empty_bundle(self):
        assert build_evidence_bundle(RawEvidence()).is_empty()

    def test_ids_are_stable_for_the_same_input(self):
        """A stored analysis must keep resolving to the same evidence."""
        first = build_evidence_bundle(RawEvidence(goals=("a", "b")))
        second = build_evidence_bundle(RawEvidence(goals=("a", "b")))

        assert first.ids == second.ids


class TestClaimAdmissibility:
    def test_a_cited_fact_is_admitted(self):
        assert check_claim(claim(), EVIDENCE) is None

    def test_a_fabricated_citation_is_rejected(self):
        """Asking a model to cite its evidence does not stop it inventing ids."""
        reason = check_claim(claim(evidence_ids=["goal:99"]), EVIDENCE)

        assert reason is RejectionReason.UNKNOWN_EVIDENCE

    def test_one_bad_citation_rejects_the_whole_claim(self):
        reason = check_claim(claim(evidence_ids=["goal:1", "goal:99"]), EVIDENCE)

        assert reason is RejectionReason.UNKNOWN_EVIDENCE

    @pytest.mark.parametrize(
        "claim_type",
        [ClaimType.FACT, ClaimType.OBSERVATION, ClaimType.INFERENCE, ClaimType.RECOMMENDATION],
    )
    def test_assertions_about_the_user_must_cite_something(self, claim_type: ClaimType):
        reason = check_claim(claim(claim_type, evidence_ids=[]), EVIDENCE)

        assert reason is RejectionReason.MISSING_EVIDENCE

    def test_uncertainty_may_cite_nothing(self):
        """An uncertainty claim is about what is absent, so it has nothing to cite."""
        reason = check_claim(claim(ClaimType.UNCERTAINTY, evidence_ids=[]), EVIDENCE)

        assert reason is None

    def test_a_hedged_statement_may_not_be_called_a_fact(self):
        """The central safety rule: inference must not be presented as fact."""
        reason = check_claim(claim(ClaimType.FACT, confidence=0.3), EVIDENCE)

        assert reason is RejectionReason.UNDERCONFIDENT_FACT

    def test_the_same_hedged_statement_is_fine_as_an_inference(self):
        assert check_claim(claim(ClaimType.INFERENCE, confidence=0.3), EVIDENCE) is None

    @pytest.mark.parametrize("confidence", [MIN_CONFIDENCE_FOR_FACT, 1.0])
    def test_a_confident_fact_is_admitted(self, confidence: float):
        assert check_claim(claim(ClaimType.FACT, confidence=confidence), EVIDENCE) is None


class TestGroundClaims:
    def test_supported_and_unsupported_claims_are_separated(self):
        report = ground_claims(
            [claim(content="kept"), claim(content="dropped", evidence_ids=["goal:99"])],
            EVIDENCE,
        )

        assert [item.content for item in report.accepted] == ["kept"]
        assert [item.claim.content for item in report.rejected] == ["dropped"]

    def test_an_unsupported_claim_is_dropped_not_downgraded(self):
        """Relabelling an invented fact as an inference would keep the invention."""
        report = ground_claims([claim(evidence_ids=["goal:99"])], EVIDENCE)

        assert report.accepted == ()
        assert not report.is_usable()

    def test_rejection_reasons_are_preserved_for_feedback(self):
        report = ground_claims(
            [
                claim(evidence_ids=["goal:99"]),
                claim(ClaimType.INFERENCE, evidence_ids=[]),
                claim(ClaimType.FACT, confidence=0.1),
            ],
            EVIDENCE,
        )

        assert {rejected.reason for rejected in report.rejected} == set(RejectionReason)

    def test_an_empty_generation_is_not_usable(self):
        assert not ground_claims([], EVIDENCE).is_usable()


class TestOverallConfidence:
    def test_no_claims_means_no_confidence(self):
        assert overall_confidence(()) == 0.0

    def test_confidence_averages_the_assertions(self):
        claims = (claim(confidence=0.8), claim(ClaimType.INFERENCE, confidence=0.6))

        assert overall_confidence(claims) == pytest.approx(0.7)

    def test_recommendations_do_not_count(self):
        """A recommendation is a proposal, not a statement about the user."""
        with_recommendation = overall_confidence(
            (claim(confidence=0.8), claim(ClaimType.RECOMMENDATION, confidence=0.1))
        )

        assert with_recommendation == pytest.approx(0.8)

    def test_recommendations_alone_yield_no_confidence(self):
        assert overall_confidence((claim(ClaimType.RECOMMENDATION, confidence=0.9),)) == 0.0


class TestPolicyValidation:
    @pytest.mark.parametrize("kwargs", [{"max_generation_attempts": 0}, {"min_claims": 0}])
    def test_incoherent_policies_are_rejected(self, kwargs: dict[str, int]):
        with pytest.raises(ValueError):
            AnalysisPolicy(**kwargs)
