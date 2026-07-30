from decision_policy import MealAction, decide_meal_action
from nutrition import (
    Confidence,
    FoodItem,
    ImageQuality,
    MealImageAnalysis,
    NutritionEstimate,
    NutritionRange,
)


def build_estimate() -> NutritionEstimate:
    nutrients = NutritionRange(minimum=10, maximum=20)
    return NutritionEstimate(
        food_items=[
            FoodItem(
                name="dal",
                portion_description="one bowl",
                calories_kcal=NutritionRange(minimum=200, maximum=300),
                protein_g=nutrients,
                carbs_g=nutrients,
                fat_g=nutrients,
                fibre_g=nutrients,
            )
        ],
        total_calories_kcal=NutritionRange(minimum=200, maximum=300),
        total_protein_g=nutrients,
        total_carbs_g=nutrients,
        total_fat_g=nutrients,
        total_fibre_g=nutrients,
    )


def build_analysis(**overrides) -> MealImageAnalysis:
    values = {
        "image_quality": ImageQuality.CLEAR,
        "visible_foods": ["dal"],
        "dish_candidates": ["dal"],
        "dish_confidence": Confidence.HIGH,
        "portion_confidence": Confidence.HIGH,
        "assumptions": [],
        "ambiguity_reason": None,
        "estimate": build_estimate(),
    }
    values.update(overrides)
    return MealImageAnalysis(**values)


def test_unusable_image_requests_new_photo():
    analysis = build_analysis(
        image_quality=ImageQuality.UNUSABLE,
        visible_foods=[],
        dish_candidates=[],
        dish_confidence=Confidence.LOW,
        portion_confidence=Confidence.LOW,
        ambiguity_reason="The photo is heavily blurred.",
        estimate=None,
    )

    decision = decide_meal_action(analysis)

    assert decision.action == MealAction.REQUEST_NEW_IMAGE
    assert "haven’t logged" in decision.user_message


def test_multiple_uncertain_candidates_asks_user_to_choose():
    analysis = build_analysis(
        dish_candidates=["dal", "sambar"],
        dish_confidence=Confidence.MEDIUM,
    )

    decision = decide_meal_action(analysis)

    assert decision.action == MealAction.ASK_DISH_CHOICE
    assert "dal, sambar" in decision.user_message


def test_low_portion_confidence_asks_for_serving_size():
    analysis = build_analysis(portion_confidence=Confidence.LOW)

    decision = decide_meal_action(analysis)

    assert decision.action == MealAction.ASK_SERVING_SIZE
    assert "serving size" in decision.reason


def test_clear_supported_analysis_logs_automatically():
    decision = decide_meal_action(build_analysis())

    assert decision.action == MealAction.AUTO_LOG
