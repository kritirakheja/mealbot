"""Loads and validates the golden meal-image dataset used by eval/run_eval.py."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CASES_PATH = GOLDEN_DIR / "cases.json"

VALID_DECISIONS = {
    "auto_log",
    "ask_dish_choice",
    "ask_serving_size",
    "request_new_image",
}


class GoldenDatasetError(Exception):
    """Raised when the golden dataset manifest is malformed."""


@dataclass(frozen=True)
class GoldenCase:
    id: str
    image_path: Path
    content_type: str
    expected_decision: str | None
    expected_calories_kcal: tuple[float, float] | None
    expected_protein_g: tuple[float, float] | None
    notes: str


def _parse_range(
    raw: list | None, field: str, case_id: str
) -> tuple[float, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 2 or raw[0] > raw[1]:
        raise GoldenDatasetError(
            f"{case_id}: {field} must be a [min, max] list with min <= max, got {raw!r}"
        )
    return (float(raw[0]), float(raw[1]))


def load_cases(cases_path: Path = CASES_PATH) -> list[GoldenCase]:
    with cases_path.open() as f:
        raw_cases = json.load(f)

    cases = []
    seen_ids: set[str] = set()
    for entry in raw_cases:
        case_id = entry["id"]
        if case_id in seen_ids:
            raise GoldenDatasetError(f"Duplicate case id: {case_id}")
        seen_ids.add(case_id)

        expected_decision = entry.get("expected_decision")
        if expected_decision is not None and expected_decision not in VALID_DECISIONS:
            raise GoldenDatasetError(
                f"{case_id}: unknown expected_decision {expected_decision!r}, "
                f"must be one of {sorted(VALID_DECISIONS)} or null"
            )

        image_path = (cases_path.parent / entry["image_path"]).resolve()
        if not image_path.exists():
            raise GoldenDatasetError(f"{case_id}: image not found at {image_path}")

        cases.append(
            GoldenCase(
                id=case_id,
                image_path=image_path,
                content_type=entry.get("content_type", "image/jpeg"),
                expected_decision=expected_decision,
                expected_calories_kcal=_parse_range(
                    entry.get("expected_calories_kcal"),
                    "expected_calories_kcal",
                    case_id,
                ),
                expected_protein_g=_parse_range(
                    entry.get("expected_protein_g"), "expected_protein_g", case_id
                ),
                notes=entry.get("notes", ""),
            )
        )
    return cases
