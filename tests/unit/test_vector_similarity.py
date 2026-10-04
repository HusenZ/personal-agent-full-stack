import pytest

from app.domain.memory.pairing import ExistingMemory, compare, pair_candidate
from app.domain.memory.policy import DEFAULT_MEMORY_POLICY
from app.domain.memory.similarity import cosine_similarity
from app.domain.memory.types import MemoryCandidate, MemoryDurability, MemoryType


def candidate(content: str, memory_type: MemoryType = MemoryType.GOAL) -> MemoryCandidate:
    return MemoryCandidate(
        content=content,
        memory_type=memory_type,
        durability=MemoryDurability.LONG_TERM,
        importance=0.8,
        confidence=0.9,
    )


class TestCosineSimilarity:
    def test_identical_vectors_are_identical(self):
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_scale_does_not_matter_only_direction(self):
        assert cosine_similarity([1.0, 2.0], [10.0, 20.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_are_unrelated(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_are_negative(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_a_zero_vector_is_not_similar_to_anything(self):
        """It has no direction, so the comparison is undefined rather than one."""
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_mismatched_widths_do_not_raise(self):
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_empty_vectors_do_not_raise(self):
        assert cosine_similarity([], []) == 0.0

    def test_the_result_never_exceeds_one(self):
        """Floating point can push an identical pair fractionally over."""
        vector = [0.1] * 1536

        assert cosine_similarity(vector, vector) <= 1.0


class TestComparePrefersVectors:
    def test_vectors_are_used_when_both_sides_have_them(self):
        """Paraphrases share no tokens here, so only the vectors can match them."""
        existing = ExistingMemory(
            id="m1",
            content="completely different words",
            memory_type=MemoryType.GOAL,
            importance=0.7,
            confidence=0.8,
            embedding=(1.0, 0.0, 0.0),
        )

        score = compare("unrelated text entirely", (1.0, 0.0, 0.0), existing)

        assert score == pytest.approx(1.0)

    def test_token_overlap_is_used_when_the_candidate_has_no_vector(self):
        existing = ExistingMemory(
            id="m1",
            content="I want to move into AI engineering",
            memory_type=MemoryType.GOAL,
            importance=0.7,
            confidence=0.8,
            embedding=(1.0, 0.0, 0.0),
        )

        score = compare("I want to move into AI engineering", None, existing)

        assert score == pytest.approx(1.0)

    def test_token_overlap_is_used_when_the_stored_memory_has_no_vector(self):
        """An embedding that failed should degrade deduplication, not remove it."""
        existing = ExistingMemory(
            id="m1",
            content="I want to move into AI engineering",
            memory_type=MemoryType.GOAL,
            importance=0.7,
            confidence=0.8,
        )

        score = compare("I want to move into AI engineering", (1.0, 0.0, 0.0), existing)

        assert score == pytest.approx(1.0)


class TestPairingWithVectors:
    def test_a_paraphrase_is_caught_as_a_duplicate_by_vector(self):
        """Token overlap scores this below the duplicate threshold; vectors do not."""
        existing = [
            ExistingMemory(
                id="m1",
                content="My goal is moving into AI engineering",
                memory_type=MemoryType.GOAL,
                importance=0.7,
                confidence=0.8,
                embedding=(1.0, 0.0, 0.0),
            )
        ]

        pairing = pair_candidate(
            candidate("I want to move into AI engineering"),
            existing,
            DEFAULT_MEMORY_POLICY,
            candidate_embedding=(0.99, 0.01, 0.0),
        )

        assert pairing.duplicate_of is not None
        assert pairing.to_judge == ()

    def test_vectors_that_disagree_still_go_to_judgement(self):
        existing = [
            ExistingMemory(
                id="m1",
                content="I have decided to stay in mobile development",
                memory_type=MemoryType.GOAL,
                importance=0.7,
                confidence=0.8,
                embedding=(0.0, 1.0, 0.0),
            )
        ]

        pairing = pair_candidate(
            candidate("I want to move into AI engineering"),
            existing,
            DEFAULT_MEMORY_POLICY,
            candidate_embedding=(1.0, 0.0, 0.0),
        )

        assert pairing.duplicate_of is None
        assert [memory.id for memory in pairing.to_judge] == ["m1"]
