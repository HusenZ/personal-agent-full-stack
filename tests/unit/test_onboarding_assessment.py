import pytest

from app.domain.onboarding.assessment import (
    assess_coverage,
    decide_completeness,
    score_dimension,
    select_next_dimension,
)
from app.domain.onboarding.context import (
    CollectedContext,
    ExtractedConstraint,
    ExtractedGoal,
    ExtractedObservation,
    ExtractedPreference,
)
from app.domain.onboarding.dimensions import ContextDimension
from app.domain.onboarding.policy import OnboardingPolicy


class TestScoreDimension:
    def test_no_evidence_scores_zero(self):
        assert score_dimension([], required_evidence=2) == 0.0

    def test_agreeing_evidence_accumulates(self):
        """Two uncertain statements that agree are worth more than either alone."""
        one = score_dimension([0.6, 0.6], required_evidence=2)
        alone = score_dimension([0.6], required_evidence=2)

        assert one > alone

    def test_a_single_item_is_penalised_when_more_is_required(self):
        assert score_dimension([1.0], required_evidence=2) == pytest.approx(0.5)

    def test_a_single_item_suffices_when_only_one_is_required(self):
        assert score_dimension([0.8], required_evidence=1) == pytest.approx(0.8)

    def test_no_amount_of_evidence_reaches_certainty(self):
        assert score_dimension([0.9] * 10, required_evidence=2) < 1.0

    def test_only_the_strongest_evidence_counts(self):
        """Padding with weak items must not dilute or inflate a good score."""
        strong = score_dimension([0.9, 0.9], required_evidence=2)
        padded = score_dimension([0.9, 0.9, 0.1, 0.1], required_evidence=2)

        assert padded == pytest.approx(strong)

    def test_score_stays_within_bounds(self):
        assert 0.0 <= score_dimension([0.5, 0.5, 0.5], required_evidence=2) <= 1.0


class TestCoverage:
    def test_empty_context_has_no_coverage(self):
        report = assess_coverage(CollectedContext())

        assert report.overall_confidence == 0.0
        assert set(report.per_dimension) == set(ContextDimension)

    def test_overall_confidence_is_bounded(self):
        context = CollectedContext(
            goals=[ExtractedGoal(name=f"g{i}", confidence=1.0) for i in range(5)],
            constraints=[ExtractedConstraint(description="c", confidence=1.0)],
            preferences=[ExtractedPreference(key="k", value="v", confidence=1.0)],
            observations=[ExtractedObservation(content=f"o{i}", confidence=1.0) for i in range(5)],
        )

        assert assess_coverage(context).overall_confidence <= 1.0

    def test_a_covered_goal_outweighs_a_covered_preference(self):
        """Goals carry the most weight, because everything later is anchored to them."""
        with_goals = assess_coverage(
            CollectedContext(goals=[ExtractedGoal(name=f"g{i}", confidence=0.9) for i in range(2)])
        )
        with_preference = assess_coverage(
            CollectedContext(preferences=[ExtractedPreference(key="k", value="v", confidence=0.9)])
        )

        assert with_goals.overall_confidence > with_preference.overall_confidence

    def test_a_partly_covered_goal_can_count_for_less_than_a_covered_preference(self):
        """Weight is importance; sufficiency is completeness. They are separate.

        One sentence about a goal leaves that dimension half explored, while one
        stated preference fully satisfies what preferences ask for. The interview
        should therefore keep probing goals rather than treat them as settled.
        """
        one_goal = assess_coverage(
            CollectedContext(goals=[ExtractedGoal(name="g", confidence=0.9)])
        )
        one_preference = assess_coverage(
            CollectedContext(preferences=[ExtractedPreference(key="k", value="v", confidence=0.9)])
        )

        assert one_goal.overall_confidence < one_preference.overall_confidence

    def test_the_interview_broadens_rather_than_drilling_one_dimension(self):
        """After a first answer about goals, the widest gap is elsewhere.

        Goals are revisited once the other dimensions have been opened up; this
        keeps a short interview from spending every question on one topic.
        """
        after_one_goal = assess_coverage(
            CollectedContext(goals=[ExtractedGoal(name="g", confidence=0.9)])
        )

        assert select_next_dimension(after_one_goal) is ContextDimension.CONTEXT


class TestNextDimension:
    def test_an_empty_context_starts_with_goals(self):
        assert select_next_dimension(assess_coverage(CollectedContext())) is ContextDimension.GOAL

    def test_a_covered_dimension_is_not_chosen_again(self):
        context = CollectedContext(
            goals=[
                ExtractedGoal(name="a", confidence=0.95),
                ExtractedGoal(name="b", confidence=0.95),
            ]
        )

        assert select_next_dimension(assess_coverage(context)) is not ContextDimension.GOAL

    def test_selection_is_reproducible(self):
        coverage = assess_coverage(CollectedContext())

        assert {select_next_dimension(coverage) for _ in range(10)} == {ContextDimension.GOAL}


class TestCompleteness:
    POLICY = OnboardingPolicy(confidence_threshold=0.75, min_questions=4, max_questions=12)

    def _confident_context(self) -> CollectedContext:
        return CollectedContext(
            goals=[ExtractedGoal(name=f"g{i}", confidence=0.95) for i in range(2)],
            constraints=[ExtractedConstraint(description="c", confidence=0.95)],
            preferences=[ExtractedPreference(key="k", value="v", confidence=0.95)],
            observations=[ExtractedObservation(content=f"o{i}", confidence=0.95) for i in range(2)],
        )

    def test_confident_context_completes_once_the_floor_is_met(self):
        decision = decide_completeness(
            assess_coverage(self._confident_context()), questions_asked=4, policy=self.POLICY
        )

        assert decision.is_complete
        assert decision.reason == "confidence_threshold_met"

    def test_confidence_alone_does_not_end_a_short_interview(self):
        """A single good answer is not a profile, however confident it sounded."""
        decision = decide_completeness(
            assess_coverage(self._confident_context()), questions_asked=1, policy=self.POLICY
        )

        assert not decision.is_complete
        assert decision.reason == "minimum_questions_not_reached"
        assert decision.next_dimension is not None

    def test_low_confidence_keeps_the_interview_going(self):
        decision = decide_completeness(
            assess_coverage(CollectedContext()), questions_asked=5, policy=self.POLICY
        )

        assert not decision.is_complete
        assert decision.reason == "confidence_below_threshold"

    def test_the_question_ceiling_always_ends_the_interview(self):
        """This bound is what guarantees the interview terminates."""
        decision = decide_completeness(
            assess_coverage(CollectedContext()), questions_asked=12, policy=self.POLICY
        )

        assert decision.is_complete
        assert decision.reason == "question_limit_reached"
        assert decision.next_dimension is None

    def test_the_ceiling_outranks_the_minimum(self):
        policy = OnboardingPolicy(confidence_threshold=0.9, min_questions=4, max_questions=4)

        decision = decide_completeness(
            assess_coverage(CollectedContext()), questions_asked=4, policy=policy
        )

        assert decision.is_complete


class TestPolicyValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"confidence_threshold": 0.0},
            {"confidence_threshold": 1.5},
            {"min_questions": 0},
            {"min_questions": 5, "max_questions": 4},
        ],
    )
    def test_incoherent_policies_are_rejected(self, kwargs: dict[str, object]):
        with pytest.raises(ValueError):
            OnboardingPolicy(**kwargs)  # type: ignore[arg-type]
