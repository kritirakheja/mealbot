#!/usr/bin/env python3
"""Run the golden meal-image eval set against the real analysis pipeline
(analyze_meal_image -> decide_meal_action) and report quality/cost/latency.

Usage:
    python -m eval.run_eval
    python -m eval.run_eval --model gemini-flash-latest --save eval/results/baseline.json
    python -m eval.run_eval --compare-to eval/results/baseline.json
    python -m eval.run_eval --prompt-file eval/candidate_prompt.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from decision_policy import decide_meal_action
from eval.golden_dataset import GoldenDatasetError, load_cases
from eval.metrics import (
    CaseResult,
    check_thresholds,
    estimated_cost_usd,
    score_case,
    summarize,
)
from nutrition import NutritionError, analyze_meal_image_with_usage

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def run(model: str | None, prompt: str | None) -> list[CaseResult]:
    cases = load_cases()
    kwargs = {}
    if model:
        kwargs["model"] = model
    if prompt:
        kwargs["prompt"] = prompt

    results = []
    for case in cases:
        print(f"[{case.id}] analyzing...")
        try:
            analysis_result = analyze_meal_image_with_usage(
                case.image_path, case.content_type, **kwargs
            )
        except NutritionError as error:
            print(f"[{case.id}] analysis failed, skipping: {error}")
            continue

        decision = decide_meal_action(analysis_result.analysis)
        results.append(score_case(case, analysis_result, decision))

    return results


def print_report(results: list[CaseResult], summary: dict) -> None:
    print()
    header = (
        f"{'case':<24} {'expected':<18} {'actual':<18} {'match':<7} "
        f"{'cal ok':<7} {'prot ok':<7} {'latency':<9} {'tokens'}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.case_id:<24} {str(r.expected_decision):<18} {r.actual_decision:<18} "
            f"{str(r.decision_match):<7} {str(r.calories_contained):<7} "
            f"{str(r.protein_contained):<7} {r.latency_seconds:<9.2f} {r.total_tokens}"
        )
        if r.false_auto_log:
            print(f"  !! FALSE AUTO-LOG on {r.case_id} (expected {r.expected_decision})")

    print()
    print("Summary:")
    print(f"  cases run:              {summary['case_count']}")
    print(f"  cases with an expectation: {summary['scored_case_count']}")
    decision_accuracy = summary["decision_accuracy"]
    print(
        "  decision accuracy:      "
        + (f"{decision_accuracy:.1%}" if decision_accuracy is not None else "n/a (no scored cases)")
    )
    print(
        f"  false auto-log rate:    {summary['false_auto_log_rate']:.1%} "
        f"({summary['false_auto_log_count']} case(s))"
        if summary["false_auto_log_rate"] is not None
        else "  false auto-log rate:    n/a"
    )
    if summary["mean_latency_seconds"] is not None:
        print(
            f"  latency (mean / p95):   "
            f"{summary['mean_latency_seconds']:.2f}s / {summary['p95_latency_seconds']:.2f}s"
        )
    print(f"  total tokens:           {summary['total_tokens']}")
    print(f"  estimated cost:         ${summary['total_cost_usd']:.4f}")


def save_results(results: list[CaseResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": [
            {**asdict(r), "estimated_cost_usd": estimated_cost_usd(r)} for r in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2))


def compare(results: list[CaseResult], baseline_path: Path) -> None:
    baseline_payload = json.loads(baseline_path.read_text())
    baseline_by_id = {r["case_id"]: r for r in baseline_payload["results"]}

    print(f"\nComparing against {baseline_path} (from {baseline_payload['generated_at']}):")
    for r in results:
        baseline = baseline_by_id.get(r.case_id)
        if baseline is None:
            print(f"  {r.case_id}: new case, no baseline to compare")
            continue

        if baseline["decision_match"] != r.decision_match:
            print(
                f"  {r.case_id}: decision_match {baseline['decision_match']} "
                f"-> {r.decision_match}"
            )
        if baseline["false_auto_log"] != r.false_auto_log:
            print(
                f"  {r.case_id}: false_auto_log {baseline['false_auto_log']} "
                f"-> {r.false_auto_log}"
            )
        latency_delta = r.latency_seconds - baseline["latency_seconds"]
        print(f"  {r.case_id}: latency {latency_delta:+.2f}s")

    missing = set(baseline_by_id) - {r.case_id for r in results}
    for case_id in missing:
        print(f"  {case_id}: present in baseline but not in this run")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Override the Gemini model name")
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="Path to a text file with an alternate NUTRITION_PROMPT to try",
    )
    parser.add_argument(
        "--save",
        default=None,
        help="Path to save JSON results (default: eval/results/<timestamp>.json)",
    )
    parser.add_argument(
        "--compare-to",
        default=None,
        help="Path to a prior saved result JSON to diff this run against",
    )
    args = parser.parse_args()

    prompt = Path(args.prompt_file).read_text() if args.prompt_file else None

    try:
        results = run(args.model, prompt)
    except GoldenDatasetError as error:
        print(f"Golden dataset error: {error}", file=sys.stderr)
        return 2

    if not results:
        print("No cases produced a result. Nothing to report.")
        return 2

    summary = summarize(results)
    print_report(results, summary)

    save_path = (
        Path(args.save)
        if args.save
        else RESULTS_DIR / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    save_results(results, save_path)
    print(f"\nSaved results to {save_path}")

    if args.compare_to:
        compare(results, Path(args.compare_to))

    violations = check_thresholds(summary)
    if violations:
        print("\nThreshold violations:")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
