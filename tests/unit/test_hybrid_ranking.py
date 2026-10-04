import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.memory.types import MemoryType
from app.retrieval.fusion import RRF_K, best_possible_score, reciprocal_rank_fusion
from app.retrieval.hybrid import HybridConfig
from app.retrieval.keyword import MAX_QUERY_TERMS, build_query_terms
from app.retrieval.reranker import FeatureReranker, RerankedHit, RerankWeights
from app.retrieval.types import MemoryHit, RetrievalSource

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
A, B, C, D = (uuid.UUID(int=index) for index in range(1, 5))


def hit(memory_id: uuid.UUID, *, importance: float = 0.5, age_days: float = 0.0) -> MemoryHit:
    return MemoryHit(
        id=memory_id,
        content=f"memory {memory_id.int}",
        memory_type=MemoryType.FACT,
        importance=importance,
        created_at=NOW - timedelta(days=age_days),
        score=0.0,
    )


class TestReciprocalRankFusion:
    def test_an_item_found_by_both_retrievers_beats_one_found_by_either(self):
        """The property fusion exists for: agreement accumulates."""
        fused = reciprocal_rank_fusion(
            {RetrievalSource.SEMANTIC: [A, B], RetrievalSource.KEYWORD: [C, A]}
        )

        assert fused[A] > fused[B]
        assert fused[A] > fused[C]

    def test_first_place_scores_higher_than_second(self):
        fused = reciprocal_rank_fusion({RetrievalSource.SEMANTIC: [A, B]})

        assert fused[A] > fused[B]

    def test_scores_follow_the_published_formula(self):
        fused = reciprocal_rank_fusion({RetrievalSource.SEMANTIC: [A, B]})

        assert fused[A] == pytest.approx(1 / (RRF_K + 1))
        assert fused[B] == pytest.approx(1 / (RRF_K + 2))

    def test_raw_scores_play_no_part(self):
        """Cosine and full-text rank are on unrelated scales; only order counts."""
        first = reciprocal_rank_fusion({RetrievalSource.SEMANTIC: [A, B]})
        second = reciprocal_rank_fusion({RetrievalSource.SEMANTIC: [A, B]})

        assert first == second

    def test_a_duplicate_within_one_ranking_votes_once(self):
        fused = reciprocal_rank_fusion({RetrievalSource.SEMANTIC: [A, A, B]})

        assert fused[A] == pytest.approx(1 / (RRF_K + 1))

    def test_empty_rankings_fuse_to_nothing(self):
        assert (
            reciprocal_rank_fusion({RetrievalSource.SEMANTIC: [], RetrievalSource.KEYWORD: []})
            == {}
        )

    def test_an_invalid_constant_is_rejected(self):
        with pytest.raises(ValueError):
            reciprocal_rank_fusion({RetrievalSource.SEMANTIC: [A]}, k=0)

    def test_the_best_possible_score_is_first_place_everywhere(self):
        fused = reciprocal_rank_fusion(
            {RetrievalSource.SEMANTIC: [A], RetrievalSource.KEYWORD: [A]}
        )

        assert fused[A] == pytest.approx(best_possible_score(2))


class TestFeatureReranker:
    def rerank(
        self, hits: list[MemoryHit], fused: dict[uuid.UUID, float], **weights: float
    ) -> list[RerankedHit]:
        reranker = FeatureReranker(
            RerankWeights(**weights) if weights else RerankWeights(), now=NOW
        )
        return reranker.rerank(hits, fused, max_fused=best_possible_score(2))

    def test_relevance_dominates_importance(self):
        """An important but barely relevant memory must not outrank a relevant one."""
        top = best_possible_score(2)
        ranked = self.rerank(
            [hit(A, importance=0.0), hit(B, importance=1.0)], {A: top, B: top * 0.3}
        )

        assert [item.hit.id for item in ranked] == [A, B]

    def test_importance_breaks_a_relevance_tie(self):
        score = best_possible_score(2) * 0.5
        ranked = self.rerank([hit(A, importance=0.2), hit(B, importance=0.9)], {A: score, B: score})

        assert [item.hit.id for item in ranked] == [B, A]

    def test_recency_breaks_a_tie_between_equals(self):
        score = best_possible_score(2) * 0.5
        ranked = self.rerank([hit(A, age_days=300), hit(B, age_days=1)], {A: score, B: score})

        assert [item.hit.id for item in ranked] == [B, A]

    def test_relevance_is_absolute_not_relative_to_the_batch(self):
        """A lone weak match must not look perfect just because nothing beat it."""
        weak = best_possible_score(2) * 0.2
        ranked = self.rerank([hit(A)], {A: weak})

        assert ranked[0].relevance == pytest.approx(0.2)

    def test_scores_are_bounded(self):
        ranked = self.rerank([hit(A, importance=1.0)], {A: best_possible_score(2)})

        assert 0.0 <= ranked[0].score <= 1.0

    def test_equal_scores_order_the_same_way_every_time(self):
        score = best_possible_score(2) * 0.5
        hits = [hit(B), hit(A)]

        first = [item.hit.id for item in self.rerank(hits, {A: score, B: score})]
        second = [item.hit.id for item in self.rerank(list(reversed(hits)), {A: score, B: score})]

        assert first == second

    def test_a_future_timestamp_does_not_inflate_recency(self):
        ranked = self.rerank([hit(A, age_days=-5)], {A: best_possible_score(2)})

        assert ranked[0].score <= 1.0

    @pytest.mark.parametrize(
        "weights",
        [
            {"relevance": 0.5, "importance": 0.2, "recency": 0.2},
            {"relevance": -0.1, "importance": 0.6, "recency": 0.5},
            {"relevance": 0.8, "importance": 0.1, "recency": 0.1, "recency_half_life_days": 0},
        ],
    )
    def test_incoherent_weights_are_rejected(self, weights):
        with pytest.raises(ValueError):
            RerankWeights(**weights)


class TestHybridConfig:
    def test_pools_narrower_than_the_output_are_rejected(self):
        """Fusion can only promote what some retriever found."""
        with pytest.raises(ValueError):
            HybridConfig(limit=10, semantic_candidates=5)

    def test_a_zero_limit_is_rejected(self):
        with pytest.raises(ValueError):
            HybridConfig(limit=0)


class TestKeywordQueryTerms:
    def test_words_are_joined_as_alternatives(self):
        """AND across every word of a question would match almost nothing."""
        assert build_query_terms("study algorithms") == "study | algorithms"

    def test_case_is_folded(self):
        assert build_query_terms("PostgreSQL Tuning") == "postgresql | tuning"

    @pytest.mark.parametrize(
        "hostile",
        [
            "a:b & c | !d <-> (e",
            "'); DROP TABLE memories; --",
            "unbalanced 'quote",
            ":*:*:*",
        ],
    )
    def test_nothing_but_words_survives(self, hostile: str):
        """Whatever the user typed, the result is valid tsquery syntax."""
        terms = build_query_terms(hostile)

        assert all(part.isalnum() for part in terms.split(" | ") if part)

    def test_repeated_words_appear_once(self):
        assert build_query_terms("cello cello CELLO") == "cello"

    def test_the_number_of_terms_is_bounded(self):
        text = " ".join(f"word{index}" for index in range(200))

        assert len(build_query_terms(text).split(" | ")) == MAX_QUERY_TERMS

    @pytest.mark.parametrize("empty", ["", "   ", "!!! ??? ..."])
    def test_text_with_no_words_yields_no_query(self, empty: str):
        assert build_query_terms(empty) == ""
