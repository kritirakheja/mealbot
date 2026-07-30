from pathlib import Path

from decision_policy import MealAction, MealDecision
from eval.golden_dataset import GoldenCase
from eval.metrics import check_thresholds, score_case, summarize
from nutrition import (
    AnalysisResult,
    Confidence,
    FoodItem,
    ImageQuality,
    MealImageAnalysis,
    NutritionEstimate,
    NutritionRange,
)

FAKE_IMAGE_PATH = Path("unused-test-image.jpg")


def make_case(
    case_id="case_1",
    expected_decision=None,
    expected_calories_kcal=None,
    expected_protein_g=None,
):
    return GoldenCase(
        id=case_id,
        image_path=FAKE_IMAGE_PATH,
        content_type="image/jpeg",
        expected_decision=expected_decision,
        expected_calories_kcal=expected_calories_kcal,
        expected_protein_g=expected_protein_g,
        notes="",
    )


def make_estimate(calories=(450, 600), protein=(15, 22)):
    return NutritionEstimate(
        food_items=[
            FoodItem(
                name="dal and rice",
                portion_description="one medium plate",
                calories_kcal=NutritionRange(minimum=calories[0], maximum=calories[1]),
                protein_g=NutritionRange(minimum=protein[0], maximum=protein[1]),
                carbs_g=NutritionRange(minimum=70, maximum=90),
                fat_g=NutritionRange(minimum=10, maximum=18),
                fibre_g=NutritionRange(minimum=8, maximum=12),
            )
        ],
        total_calories_kcal=NutritionRange(minimum=calories[0], maximum=calories[1]),
        total_protein_g=NutritionRange(minimum=protein[0], maximum=protein[1]),
        total_carbs_g=NutritionRange(minimum=70, maximum=90),
        total_fat_g=NutritionRange(minimum=10, maximum=18),
        total_fibre_g=NutritionRange(minimum=8, maximum=12),
    )


def make_analysis_result(estimate=None, latency=1.0, total_tokens=100):
    analysis = MealImageAnalysis(
        image_quality=ImageQuality.CLEAR,
        visible_foods=["dal", "rice"],
        dish_candidates=["dal and rice"],
        dish_confidence=Confidence.HIGH,
        portion_confidence=Confidence.HIGH,
        assumptions=[],
        estimate=estimate,
    )
    return AnalysisResult(
        analysis=analysis,
        latency_seconds=latency,
        input_tokens=total_tokens - 20,
        output_tokens=20,
        total_tokens=total_tokens,
    )


def test_decision_match_true_when_action_matches_expectation():
    case = make_case(expected_decision="auto_log")
    result = make_analysis_result(estimate=make_estimate())
    decision = MealDecision(MealAction.AUTO_LOG, "clear", "")

    scored = score_case(case, result, decision)

    assert scored.decision_match is True
    assert scored.false_auto_log is False


def test_decision_match_none_when_no_expectation_set():
    case = make_case(expected_decision=None)
    result = make_analysis_result(estimate=make_estimate())
    decision = MealDecision(MealAction.AUTO_LOG, "clear", "")

    scored = score_case(case, result, decision)

    assert scored.decision_match is None
    assert scored.false_auto_log is False


def test_false_auto_log_flagged_when_model_logs_but_expected_a_refusal():
    case = make_case(expected_decision="ask_dish_choice")
    result = make_analysis_result(estimate=make_estimate())
    decision = MealDecision(MealAction.AUTO_LOG, "over-confident", "")

    scored = score_case(case, result, decision)

    assert scored.decision_match is False
    assert scored.false_auto_log is True


def test_no_range_check_when_decision_is_not_auto_log():
    case = make_case(
        expected_decision="ask_dish_choice",
        expected_calories_kcal=(400, 500),
    )
    result = make_analysis_result(estimate=None)
    decision = MealDecision(MealAction.ASK_DISH_CHOICE, "ambiguous", "which dish?")

    scored = score_case(case, result, decision)

    assert scored.calories_contained is None
    assert scored.protein_contained is None


def test_calories_contained_true_when_expected_range_inside_model_range():
    case = make_case(
        expected_decision="auto_log",
        expected_calories_kcal=(475, 550),  # inside the model's (450, 600)
    )
    result = make_analysis_result(estimate=make_estimate(calories=(450, 600)))
    decision = MealDecision(MealAction.AUTO_LOG, "clear", "")

    scored = score_case(case, result, decision)

    assert scored.calories_contained is True


def test_calories_contained_false_when_expected_range_wider_than_model_range():
    case = make_case(
        expected_decision="auto_log",
        expected_calories_kcal=(300, 700),  # wider than the model's (450, 600)
    )
    result = make_analysis_result(estimate=make_estimate(calories=(450, 600)))
    decision = MealDecision(MealAction.AUTO_LOG, "clear", "")

    scored = score_case(case, result, decision)

    assert scored.calories_contained is False


def test_summarize_computes_accuracy_and_false_auto_log_rate():
    case_a = make_case("a", expected_decision="auto_log")
    case_b = make_case("b", expected_decision="ask_dish_choice")
    result_a = make_analysis_result(estimate=make_estimate(), latency=1.0)
    result_b = make_analysis_result(estimate=make_estimate(), latency=3.0)

    scored_a = score_case(case_a, result_a, MealDecision(MealAction.AUTO_LOG, "", ""))
    scored_b = score_case(
        case_b, result_b, MealDecision(MealAction.AUTO_LOG, "", "")
    )  # wrong: model logs when it should have asked

    summary = summarize([scored_a, scored_b])

    assert summary["case_count"] == 2
    assert summary["scored_case_count"] == 2
    assert summary["decision_accuracy"] == 0.5
    assert summary["false_auto_log_count"] == 1
    assert summary["false_auto_log_rate"] == 0.5
    assert summary["mean_latency_seconds"] == 2.0


def test_check_thresholds_flags_false_auto_log_and_low_accuracy():
    summary = {
        "false_auto_log_rate": 0.5,
        "decision_accuracy": 0.5,
    }

    violations = check_thresholds(summary)

    assert len(violations) == 2


def test_check_thresholds_ignores_none_metrics():
    summary = {
        "false_auto_log_rate": None,
        "decision_accuracy": None,
    }

    violations = check_thresholds(summary)

    assert violations == []
