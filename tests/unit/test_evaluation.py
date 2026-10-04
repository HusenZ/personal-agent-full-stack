import math
from typing import Any

import pytest
from pydantic import ValidationError

from app.evaluation.answers import numbers_in, states_figures
from app.evaluation.dataset import Dataset, load_dataset
from app.evaluation.metrics import (
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from app.evaluation.report import comparability_warnings, compare_reports

RELEVANCE = {"a": 2, "b": 1}


class TestRankingMetrics:
    def test_recall_counts_relevant_items_found_in_the_top_k(self):
        assert recall_at_k(["x", "a", "y", "b"], RELEVANCE, k=2) == 0.5
        assert recall_at_k(["x", "a", "y", "b"], RELEVANCE, k=4) == 1.0

    def test_precision_divides_by_k_not_by_what_was_returned(self):
        """Returning one right answer where three were asked for is not perfect."""
        assert precision_at_k(["a"], RELEVANCE, k=3) == pytest.approx(1 / 3)

    def test_reciprocal_rank_is_one_over_the_first_relevant_position(self):
        assert reciprocal_rank(["x", "y", "b"], RELEVANCE) == pytest.approx(1 / 3)
        assert reciprocal_rank(["x", "y"], RELEVANCE) == 0.0

    def test_ndcg_is_one_for_the_ideal_ordering(self):
        assert ndcg_at_k(["a", "b"], RELEVANCE, k=2) == pytest.approx(1.0)

    def test_ndcg_prefers_the_answer_above_what_merely_helps(self):
        best = ndcg_at_k(["a", "b"], RELEVANCE, k=2)
        swapped = ndcg_at_k(["b", "a"], RELEVANCE, k=2)

        assert swapped < best
        # Hand-computed: (1 + 3/log2 3) / (3 + 1/log2 3).
        expected = (1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3))
        assert swapped == pytest.approx(expected)

    @pytest.mark.parametrize("metric", [recall_at_k, ndcg_at_k])
    def test_metrics_undefined_without_relevant_items_refuse(self, metric: Any):
        with pytest.raises(ValueError):
            metric(["a"], {}, 5)

    def test_percentile_is_nearest_rank(self):
        values = [float(value) for value in range(1, 101)]

        assert percentile(values, 50) == 50.0
        assert percentile(values, 95) == 95.0
        assert percentile([7.0], 95) == 7.0


class TestFigures:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("You walked 23,700 steps.", [23700.0]),
            ("2.5 hours over 2 sessions", [2.5, 2.0]),
            ("In 2026 you ran 1,234.5 km", [2026.0, 1234.5]),
            ("no numbers here", []),
        ],
    )
    def test_numbers_are_read_with_separators_and_decimals(self, text: str, expected: list[float]):
        assert numbers_in(text) == expected

    def test_every_expected_figure_must_be_stated(self):
        assert states_figures("You ran 25 km over 3 runs.", [25])
        assert not states_figures("You ran about 24 km.", [25])
        assert not states_figures("You ran 25 km.", [25, 3])


def report(kind: str, summary: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "dataset": "d",
        "dataset_version": "1",
        "config": {"k": 6},
        "summary": summary,
    } | overrides


class TestComparison:
    def test_each_metric_is_judged_in_its_own_direction(self):
        baseline = report("answers", {"grounded_rate": 0.8, "latency_p95_ms": 900.0, "cases": 10})
        candidate = report("answers", {"grounded_rate": 0.9, "latency_p95_ms": 1200.0, "cases": 10})

        verdicts = {row["metric"]: row["verdict"] for row in compare_reports(baseline, candidate)}

        assert verdicts == {"grounded_rate": "better", "latency_p95_ms": "worse"}

    def test_retrieval_reports_are_compared_per_strategy(self):
        baseline = report("retrieval", {"hybrid": {"mrr": 0.7}, "vector": {"mrr": 0.5}})
        candidate = report("retrieval", {"hybrid": {"mrr": 0.7}, "vector": {"mrr": 0.6}})

        rows = compare_reports(baseline, candidate)

        assert [(row["group"], row["verdict"]) for row in rows] == [
            ("hybrid", "same"),
            ("vector", "better"),
        ]

    def test_reports_of_different_kinds_cannot_be_compared(self):
        with pytest.raises(ValueError):
            compare_reports(report("answers", {}), report("retrieval", {}))

    def test_differences_in_what_was_measured_are_warned_about(self):
        warnings = comparability_warnings(
            report("answers", {}),
            report("answers", {}, dataset_version="2", config={"k": 10}),
        )

        assert len(warnings) == 2


def minimal(**case: Any) -> dict[str, Any]:
    return {
        "name": "t",
        "version": "1",
        "personas": [
            {"key": "p", "memories": [{"key": "m", "content": "x", "memory_type": "fact"}]}
        ],
        "cases": [
            {"id": "c", "persona": "p", "question": "q", "category": "direct", "relevant": {"m": 2}}
            | case
        ],
    }


class TestDatasetValidation:
    def test_the_committed_dataset_is_valid(self):
        dataset = load_dataset("personas-v1")

        assert len(dataset.cases) >= 40
        assert not dataset.reviewed

    @pytest.mark.parametrize(
        "case",
        [
            {"relevant": {"missing": 2}},
            {"persona": "nobody"},
            {"relevant": {"m": 3}},
            {"category": "unanswerable"},
            {"relevant": {}},
        ],
    )
    def test_labels_that_cannot_be_right_are_rejected(self, case: dict[str, Any]):
        with pytest.raises(ValidationError):
            Dataset.model_validate(minimal(**case))

    def test_duplicate_case_ids_are_rejected(self):
        data = minimal()
        data["cases"].append(dict(data["cases"][0]))

        with pytest.raises(ValidationError):
            Dataset.model_validate(data)
