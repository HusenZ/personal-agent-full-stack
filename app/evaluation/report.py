"""Reading reports back: summaries for a terminal, and baseline-to-candidate deltas."""

import json
from pathlib import Path
from typing import Any

# Metrics where lower is better.
LOWER_IS_BETTER = frozenset(
    {
        "error_rate",
        "degraded_rate",
        "noise_on_unanswerable",
        "latency_p50_ms",
        "latency_p95_ms",
        "model_calls_mean",
        "input_tokens_mean",
        "output_tokens_mean",
        "cost_per_request_usd",
    }
)

# Counts describe the run rather than score it.
_NOT_SCORED = frozenset({"cases"})


def load_report(path: Path) -> dict[str, Any]:
    report: dict[str, Any] = json.loads(path.read_text())
    return report


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def summary_groups(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The report's headline numbers, keyed by strategy or as a single group."""
    summary: dict[str, Any] = report["summary"]
    if report["kind"] == "retrieval":
        return {name: dict(values) for name, values in summary.items()}
    return {"answers": dict(summary)}


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Every metric present in both, with its change and whether that is better."""
    if baseline["kind"] != candidate["kind"]:
        raise ValueError("cannot compare a retrieval report with an answers report")

    rows: list[dict[str, Any]] = []
    before, after = summary_groups(baseline), summary_groups(candidate)
    for group in sorted(set(before) & set(after)):
        for metric in sorted(set(before[group]) & set(after[group]) - _NOT_SCORED):
            old, new = before[group][metric], after[group][metric]
            if not isinstance(old, int | float) or not isinstance(new, int | float):
                continue
            delta = new - old
            improved = delta < 0 if metric in LOWER_IS_BETTER else delta > 0
            rows.append(
                {
                    "group": group,
                    "metric": metric,
                    "baseline": old,
                    "candidate": new,
                    "delta": round(delta, 6),
                    "verdict": "same" if delta == 0 else ("better" if improved else "worse"),
                }
            )
    return rows


def comparability_warnings(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """Reasons the two reports may not be measuring the same thing."""
    warnings = []
    if baseline["dataset"] != candidate["dataset"]:
        warnings.append("the reports use different datasets")
    elif baseline["dataset_version"] != candidate["dataset_version"]:
        warnings.append("the reports use different versions of the dataset")
    if baseline.get("config") != candidate.get("config"):
        warnings.append("the reports were run with different configuration")
    return warnings


def format_table(rows: list[list[str]]) -> str:
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    )
