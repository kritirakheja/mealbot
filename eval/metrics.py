"""Scoring logic for comparing a real analysis pipeline result against a
GoldenCase's expectations."""

from __future__ import annotations

from dataclasses import dataclass

from decision_policy import MealAction, MealDecision
from eval.golden_dataset import GoldenCase
from nutrition import AnalysisResult

# Cost isn't computed automatically — token counts are always reported, but
# converting to a dollar figure needs current pricing. Fill these in with
# your model's per-1K-token price (input/output) if you want a $ estimate;
# left at 0 means the report just shows "$0.00" alongside the raw tokens.
PRICE_PER_1K_INPUT_TOKENS = 0.0
PRICE_PER_1K_OUTPUT_TOKENS = 0.0

ACTION_TO_LABEL = {
    MealAction.AUTO_LOG: "auto_log",
    MealAction.ASK_DISH_CHOICE: "ask_dish_choice",
    MealAction.ASK_SERVING_SIZE: "ask_serving_size",
    MealAction.REQUEST_NEW_IMAGE: "request_new_image",
}


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    actual_decision: str
    expected_decision: str | None
    decision_match: bool | None
    false_auto_log: bool
    calories_contained: bool | None
    protein_contained: bool | None
    latency_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    notes: str


def _contained(
    expected: tuple[float, float] | None,
    actual: tuple[float, float] | None,
) -> bool | None:
    """True iff `expected` falls entirely inside `actual` (strict containment)."""
    if expected is None or actual is None:
        return None
    expected_min, expected_max = expected
    actual_min, actual_max = actual
    return actual_min <= expected_min and expected_max <= actual_max


def score_case(
    case: GoldenCase,
    result: AnalysisResult,
    decision: MealDecision,
) -> CaseResult:
    actual_label = ACTION_TO_LABEL[decision.action]

    decision_match = (
        None
        if case.expected_decision is None
        else actual_label == case.expected_decision
    )
    false_auto_log = (
        actual_label == "auto_log"
        and case.expected_decision is not None
        and case.expected_decision != "auto_log"
    )

    calories_contained = None
    protein_contained = None
    estimate = result.analysis.estimate
    if actual_label == "auto_log" and estimate is not None:
        calories_contained = _contained(
            case.expected_calories_kcal,
            (
                estimate.total_calories_kcal.minimum,
                estimate.total_calories_kcal.maximum,
            ),
        )
        protein_contained = _contained(
            case.expected_protein_g,
            (estimate.total_protein_g.minimum, estimate.total_protein_g.maximum),
        )

    return CaseResult(
        case_id=case.id,
        actual_decision=actual_label,
        expected_decision=case.expected_decision,
        decision_match=decision_match,
        false_auto_log=false_auto_log,
        calories_contained=calories_contained,
        protein_contained=protein_contained,
        latency_seconds=result.latency_seconds,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        notes=case.notes,
    )


def estimated_cost_usd(result: CaseResult) -> float:
    input_cost = (result.input_tokens or 0) / 1000 * PRICE_PER_1K_INPUT_TOKENS
    output_cost = (result.output_tokens or 0) / 1000 * PRICE_PER_1K_OUTPUT_TOKENS
    return input_cost + output_cost


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, int(len(sorted_values) * pct))
    return sorted_values[index]


def summarize(results: list[CaseResult]) -> dict:
    scored = [r for r in results if r.decision_match is not None]
    false_auto_log_count = sum(1 for r in results if r.false_auto_log)
    latencies = sorted(r.latency_seconds for r in results)
    total_tokens = sum(r.total_tokens or 0 for r in results)
    total_cost_usd = sum(estimated_cost_usd(r) for r in results)

    return {
        "case_count": len(results),
        "scored_case_count": len(scored),
        "decision_accuracy": (
            sum(1 for r in scored if r.decision_match) / len(scored)
            if scored
            else None
        ),
        "false_auto_log_count": false_auto_log_count,
        "false_auto_log_rate": (
            false_auto_log_count / len(results) if results else None
        ),
        "mean_latency_seconds": (
            sum(latencies) / len(latencies) if latencies else None
        ),
        "p95_latency_seconds": _percentile(latencies, 0.95),
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost_usd,
    }


# Threshold constants for run_eval.py's exit code. Not wired into CI yet,
# but a future workflow can just check the runner's exit status.
MAX_FALSE_AUTO_LOG_RATE = 0.0
MIN_DECISION_ACCURACY = 0.8


def check_thresholds(summary: dict) -> list[str]:
    """Return a list of human-readable threshold violations (empty = pass).
    A None metric (nothing scored yet) is never treated as a violation."""
    violations = []

    false_auto_log_rate = summary["false_auto_log_rate"]
    if false_auto_log_rate is not None and false_auto_log_rate > MAX_FALSE_AUTO_LOG_RATE:
        violations.append(
            f"false_auto_log_rate {false_auto_log_rate:.2%} exceeds "
            f"{MAX_FALSE_AUTO_LOG_RATE:.2%}"
        )

    decision_accuracy = summary["decision_accuracy"]
    if decision_accuracy is not None and decision_accuracy < MIN_DECISION_ACCURACY:
        violations.append(
            f"decision_accuracy {decision_accuracy:.2%} below "
            f"{MIN_DECISION_ACCURACY:.2%}"
        )

    return violations
