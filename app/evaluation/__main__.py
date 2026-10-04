"""Run the evaluation against the configured model and database.

    uv run python -m app.evaluation retrieval [--dataset personas-v1]
    uv run python -m app.evaluation answers [--judge] [--input-price 0.4 --output-price 1.6]
    uv run python -m app.evaluation compare BASELINE.json CANDIDATE.json

Creates synthetic accounts in the configured database and deletes them when it
finishes; point DATABASE_URL at a database meant for this. Calls the configured
model, which costs money. Fake-model runs belong to the test suite.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.evaluation.answers import Prices, evaluate_answers
from app.evaluation.dataset import load_dataset
from app.evaluation.report import (
    comparability_warnings,
    compare_reports,
    format_table,
    load_report,
    summary_groups,
    write_report,
)
from app.evaluation.retrieval import evaluate_retrieval
from app.main import create_app
from app.retrieval.hybrid import HybridConfig

RESULTS_DIRECTORY = Path(__file__).resolve().parents[2] / "evaluation" / "results"


def _evaluation_settings() -> Settings:
    settings = get_settings()
    if settings.is_production:
        raise SystemExit("refusing to evaluate against a production environment")
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        raise SystemExit("OPENAI_API_KEY is required: evaluation measures the real model")
    # Extraction and limits would change or throttle the evaluation run.
    return settings.model_copy(
        update={
            "memory_extraction_enabled": False,
            "rate_limit_enabled": False,
            "usage_limits_enabled": False,
        }
    )


async def _run(kind: str, arguments: argparse.Namespace) -> dict[str, Any]:
    settings = _evaluation_settings()
    app = create_app(settings)
    dataset = load_dataset(arguments.dataset)
    embeddings = app.state.embedding_provider
    try:
        if kind == "retrieval":
            return await evaluate_retrieval(
                app,
                dataset,
                embeddings=embeddings,
                embedding_model=settings.embedding_model,
                config=HybridConfig.for_limit(
                    settings.memory_retrieval_limit, settings.memory_similarity_threshold
                ),
            )
        prices = (
            Prices(arguments.input_price, arguments.output_price)
            if arguments.input_price is not None and arguments.output_price is not None
            else None
        )
        report = await evaluate_answers(
            app,
            dataset,
            embeddings=embeddings,
            embedding_model=settings.embedding_model,
            judge=app.state.llm_provider if arguments.judge else None,
            prices=prices,
        )
        report["config"]["llm_model"] = settings.llm_model
        return report
    finally:
        await app.state.database_engine.dispose()


def _print_summary(report: dict[str, Any]) -> None:
    groups = summary_groups(report)
    metrics = sorted({metric for values in groups.values() for metric in values})
    rows = [["metric", *groups]]
    rows += [
        [metric, *(str(values.get(metric, "")) for values in groups.values())] for metric in metrics
    ]
    print(format_table(rows))
    if not report["dataset_reviewed"]:
        print("\nNote: this dataset's labels have not been reviewed by a person.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation")
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("retrieval", "answers"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", default="personas-v1")
        command.add_argument("--output", type=Path)
        if name == "answers":
            command.add_argument(
                "--judge", action="store_true", help="score replies with the model"
            )
            command.add_argument("--input-price", type=float, help="USD per million input tokens")
            command.add_argument("--output-price", type=float, help="USD per million output tokens")

    compare = commands.add_parser("compare")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)

    arguments = parser.parse_args()

    if arguments.command == "compare":
        baseline, candidate = load_report(arguments.baseline), load_report(arguments.candidate)
        for warning in comparability_warnings(baseline, candidate):
            print(f"Warning: {warning}", file=sys.stderr)
        rows = [["group", "metric", "baseline", "candidate", "delta", "verdict"]]
        rows += [
            [
                row["group"],
                row["metric"],
                str(row["baseline"]),
                str(row["candidate"]),
                f"{row['delta']:+g}",
                row["verdict"],
            ]
            for row in compare_reports(baseline, candidate)
        ]
        print(format_table(rows))
        return

    report = asyncio.run(_run(arguments.command, arguments))
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    output = arguments.output or RESULTS_DIRECTORY / f"{arguments.command}-{stamp}.json"
    write_report(report, output)
    _print_summary(report)
    print(f"\nWritten to {output}")


if __name__ == "__main__":
    main()
