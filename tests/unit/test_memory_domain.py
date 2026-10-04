import pytest

from app.domain.memory.decisions import (
    DecisionKind,
    MemoryJudgment,
    MemoryVerdict,
    PairJudgment,
    admit,
    decide,
    normalise_durability,
    reinforced_scores,
)
from app.domain.memory.pairing import CandidatePairing, ExistingMemory, pair_candidate
from app.domain.memory.policy import DEFAULT_MEMORY_POLICY, MemoryPolicy
from app.domain.memory.similarity import (
    DUPLICATE_THRESHOLD,
    RELATED_THRESHOLD,
    similarity,
    tokenise,
)
from app.domain.memory.types import MemoryCandidate, MemoryDurability, MemoryType


def candidate(
    content: str = "I want to move into AI engineering",
    memory_type: MemoryType = MemoryType.GOAL,
    *,
    importance: float = 0.8,
    confidence: float = 0.9,
    durability: MemoryDurability = MemoryDurability.LONG_TERM,
) -> MemoryCandidate:
    return MemoryCandidate(
        content=content,
        memory_type=memory_type,
        durability=durability,
        importance=importance,
        confidence=confidence,
    )


def existing(
    memory_id: str,
    content: str,
    memory_type: MemoryType = MemoryType.GOAL,
    *,
    importance: float = 0.7,
    confidence: float = 0.8,
) -> ExistingMemory:
    return ExistingMemory(
        id=memory_id,
        content=content,
        memory_type=memory_type,
        importance=importance,
        confidence=confidence,
    )


class TestSimilarity:
    def test_identical_text_is_identical(self):
        assert similarity("I like tea", "I like tea") == 1.0

    def test_unrelated_text_does_not_overlap(self):
        assert similarity("I have a dog named Rex", "I want to learn Rust") == 0.0

    def test_stopwords_do_not_create_similarity(self):
        """Without removing them, two unrelated sentences share "i", "the", "to"."""
        assert similarity("I went to the shop", "I have to be at the office") == 0.0

    def test_case_and_punctuation_are_ignored(self):
        assert similarity("I like TEA!", "i like tea") == 1.0

    def test_a_paraphrase_is_related_but_not_duplicate(self):
        score = similarity(
            "I want to move into AI engineering", "My goal is moving into AI engineering"
        )

        assert RELATED_THRESHOLD <= score < DUPLICATE_THRESHOLD

    def test_empty_text_is_not_similar_to_anything(self):
        assert similarity("", "I like tea") == 0.0

    def test_similarity_is_symmetric(self):
        assert similarity("a dog and a cat", "a cat") == similarity("a cat", "a dog and a cat")

    def test_tokenising_drops_stopwords(self):
        assert tokenise("I want to be a developer") == {"want", "developer"}


class TestPairing:
    def test_a_restatement_is_settled_without_a_model(self):
        """Identical text does not need judgement, and should not cost a call."""
        pairing = pair_candidate(
            candidate(),
            [existing("m1", "I want to move into AI engineering")],
            DEFAULT_MEMORY_POLICY,
        )

        assert pairing.duplicate_of is not None
        assert pairing.to_judge == ()

    def test_a_duplicate_of_another_type_is_not_a_duplicate(self):
        pairing = pair_candidate(
            candidate(),
            [existing("m1", "I want to move into AI engineering", MemoryType.OBSERVATION)],
            DEFAULT_MEMORY_POLICY,
        )

        assert pairing.duplicate_of is None

    def test_an_unrelated_memory_is_not_worth_judging(self):
        pairing = pair_candidate(
            candidate("I have a cat named Milo", MemoryType.FACT),
            [existing("m1", "I want to move into AI engineering")],
            DEFAULT_MEMORY_POLICY,
        )

        assert pairing.to_judge == ()

    def test_a_paraphrase_is_offered_for_judgement(self):
        pairing = pair_candidate(
            candidate(),
            [existing("m1", "My goal is moving into AI engineering")],
            DEFAULT_MEMORY_POLICY,
        )

        assert [memory.id for memory in pairing.to_judge] == ["m1"]

    def test_a_contradiction_with_no_shared_words_is_still_caught(self):
        """This is the case lexical overlap cannot see, and the one superseding
        exists for: two goals that cannot both be true, sharing no vocabulary."""
        pairing = pair_candidate(
            candidate("I want to move into AI engineering"),
            [existing("m1", "I have decided to stay in mobile development")],
            DEFAULT_MEMORY_POLICY,
        )

        assert (
            similarity(
                "I want to move into AI engineering", "I have decided to stay in mobile development"
            )
            < RELATED_THRESHOLD
        )
        assert [memory.id for memory in pairing.to_judge] == ["m1"]

    def test_same_type_comparison_does_not_apply_to_every_type(self):
        """A new fact does not plausibly replace an unrelated old fact."""
        pairing = pair_candidate(
            candidate("I have a cat named Milo", MemoryType.FACT),
            [existing("m1", "I once lived in Berlin", MemoryType.FACT)],
            DEFAULT_MEMORY_POLICY,
        )

        assert pairing.to_judge == ()

    def test_the_number_of_pairs_judged_is_bounded(self):
        policy = MemoryPolicy(max_judged_pairs=3)
        many = [existing(f"m{index}", f"Goal number {index}") for index in range(20)]

        pairing = pair_candidate(candidate(), many, policy)

        assert len(pairing.to_judge) == 3

    def test_the_number_of_memories_compared_is_bounded(self):
        policy = MemoryPolicy(max_comparisons=2, max_judged_pairs=50)
        many = [existing(f"m{index}", f"Goal number {index}") for index in range(50)]

        pairing = pair_candidate(candidate(), many, policy)

        assert len(pairing.to_judge) <= 2

    def test_pairing_is_reproducible(self):
        many = [existing(f"m{index}", f"Goal number {index}") for index in range(10)]

        first = pair_candidate(candidate(), many, DEFAULT_MEMORY_POLICY)
        second = pair_candidate(candidate(), many, DEFAULT_MEMORY_POLICY)

        assert [m.id for m in first.to_judge] == [m.id for m in second.to_judge]


class TestAdmission:
    def test_a_strong_candidate_is_admitted(self):
        assert admit(candidate(), DEFAULT_MEMORY_POLICY)

    def test_an_unimportant_candidate_is_dropped(self):
        """It would dilute every later retrieval without earning its place."""
        assert not admit(candidate(importance=0.05), DEFAULT_MEMORY_POLICY)

    def test_an_uncertain_candidate_is_dropped(self):
        """A guess about the user is worse than no memory of them."""
        assert not admit(candidate(confidence=0.1), DEFAULT_MEMORY_POLICY)

    @pytest.mark.parametrize(
        "kwargs", [{"min_importance": 1.5}, {"min_confidence": -1}, {"max_comparisons": 0}]
    )
    def test_incoherent_policies_are_rejected(self, kwargs: dict[str, float]):
        with pytest.raises(ValueError):
            MemoryPolicy(**kwargs)  # type: ignore[arg-type]


class TestDurability:
    def test_a_goal_is_always_long_term(self):
        """Left to the model, a stated goal occasionally expires in a day."""
        fixed = normalise_durability(candidate(durability=MemoryDurability.TEMPORARY))

        assert fixed.durability is MemoryDurability.LONG_TERM

    def test_temporary_context_is_always_temporary(self):
        fixed = normalise_durability(
            candidate(
                "I am on a train right now",
                MemoryType.TEMPORARY_CONTEXT,
                durability=MemoryDurability.LONG_TERM,
            )
        )

        assert fixed.durability is MemoryDurability.TEMPORARY

    def test_an_unconstrained_type_keeps_what_the_model_chose(self):
        fixed = normalise_durability(
            candidate("I ran a marathon", MemoryType.EVENT, durability=MemoryDurability.SHORT_TERM)
        )

        assert fixed.durability is MemoryDurability.SHORT_TERM


class TestDecisions:
    def test_a_lexical_duplicate_reinforces(self):
        pairing = CandidatePairing(candidate=candidate(), duplicate_of=existing("m1", "same"))

        assert decide(pairing).kind is DecisionKind.REINFORCE

    def test_nothing_to_compare_against_creates(self):
        assert decide(CandidatePairing(candidate=candidate())).kind is DecisionKind.CREATE

    def test_a_contradiction_supersedes(self):
        old = existing("m1", "I want to stay in mobile development")
        judgment = MemoryJudgment(
            judgments=[PairJudgment(memory_id="m1", verdict=MemoryVerdict.CONTRADICTS)]
        )

        decision = decide(CandidatePairing(candidate=candidate(), to_judge=(old,)), judgment)

        assert decision.kind is DecisionKind.SUPERSEDE
        assert [memory.id for memory in decision.supersedes] == ["m1"]

    def test_several_contradictions_are_all_superseded(self):
        old = (existing("m1", "one"), existing("m2", "two"))
        judgment = MemoryJudgment(
            judgments=[
                PairJudgment(memory_id="m1", verdict=MemoryVerdict.CONTRADICTS),
                PairJudgment(memory_id="m2", verdict=MemoryVerdict.CONTRADICTS),
            ]
        )

        decision = decide(CandidatePairing(candidate=candidate(), to_judge=old), judgment)

        assert {memory.id for memory in decision.supersedes} == {"m1", "m2"}

    def test_a_semantic_duplicate_reinforces(self):
        old = existing("m1", "My goal is moving into AI engineering")
        judgment = MemoryJudgment(
            judgments=[PairJudgment(memory_id="m1", verdict=MemoryVerdict.DUPLICATE)]
        )

        decision = decide(CandidatePairing(candidate=candidate(), to_judge=(old,)), judgment)

        assert decision.kind is DecisionKind.REINFORCE

    def test_unrelated_verdicts_create(self):
        old = existing("m1", "something else")
        judgment = MemoryJudgment(
            judgments=[PairJudgment(memory_id="m1", verdict=MemoryVerdict.UNRELATED)]
        )

        decision = decide(CandidatePairing(candidate=candidate(), to_judge=(old,)), judgment)

        assert decision.kind is DecisionKind.CREATE

    def test_a_verdict_on_a_memory_that_was_not_offered_is_ignored(self):
        """A model asked to judge a list will occasionally judge something else."""
        old = existing("m1", "something")
        judgment = MemoryJudgment(
            judgments=[PairJudgment(memory_id="ghost", verdict=MemoryVerdict.CONTRADICTS)]
        )

        decision = decide(CandidatePairing(candidate=candidate(), to_judge=(old,)), judgment)

        assert decision.kind is DecisionKind.CREATE

    def test_a_missing_judgement_creates_rather_than_supersedes(self):
        """Superseding on the strength of a failed call is the worse mistake."""
        old = existing("m1", "something")

        decision = decide(CandidatePairing(candidate=candidate(), to_judge=(old,)), None)

        assert decision.kind is DecisionKind.CREATE


class TestReinforcement:
    def test_hearing_something_again_never_weakens_it(self):
        old = existing("m1", "same", importance=0.9, confidence=0.95)

        importance, confidence = reinforced_scores(old, candidate(importance=0.2, confidence=0.3))

        assert (importance, confidence) == (0.9, 0.95)

    def test_a_stronger_restatement_raises_the_scores(self):
        old = existing("m1", "same", importance=0.4, confidence=0.5)

        importance, confidence = reinforced_scores(old, candidate(importance=0.9, confidence=0.95))

        assert (importance, confidence) == (0.9, 0.95)
