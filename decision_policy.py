from dataclasses import dataclass
from enum import Enum

from nutrition import Confidence, ImageQuality, MealImageAnalysis


class MealAction(str, Enum):
    AUTO_LOG = "auto_log"
    ASK_SERVING_SIZE = "ask_serving_size"
    ASK_DISH_CHOICE = "ask_dish_choice"
    REQUEST_NEW_IMAGE = "request_new_image"


@dataclass(frozen=True)
class MealDecision:
    action: MealAction
    reason: str
    user_message: str


def decide_meal_action(analysis: MealImageAnalysis) -> MealDecision:
    if (
        analysis.image_quality == ImageQuality.UNUSABLE
        or not analysis.visible_foods
    ):
        reason = (
            analysis.ambiguity_reason
            or "The food is not clear enough to identify reliably."
        )
        return MealDecision(
            action=MealAction.REQUEST_NEW_IMAGE,
            reason=reason,
            user_message=(
                "I can’t identify the meal reliably from this photo, so I "
                "haven’t logged an estimate. Please send a clearer photo "
                "with the full plate visible."
            ),
        )

    if (
        len(analysis.dish_candidates) > 1
        and analysis.dish_confidence != Confidence.HIGH
    ):
        choices = ", ".join(analysis.dish_candidates)
        return MealDecision(
            action=MealAction.ASK_DISH_CHOICE,
            reason="Several dishes plausibly match the image.",
            user_message=f"Which of these best matches your meal: {choices}?",
        )

    if analysis.portion_confidence == Confidence.LOW:
        return MealDecision(
            action=MealAction.ASK_SERVING_SIZE,
            reason="The serving size is not visible enough to estimate.",
            user_message=(
                "I can identify the food, but not the serving size. "
                "How much did you have—for example, one bowl or two rotis?"
            ),
        )

    if analysis.estimate is None:
        return MealDecision(
            action=MealAction.REQUEST_NEW_IMAGE,
            reason="The analysis did not contain a defensible estimate.",
            user_message=(
                "I don’t have enough information to estimate this meal "
                "reliably. Please send a clearer photo."
            ),
        )

    return MealDecision(
        action=MealAction.AUTO_LOG,
        reason="The dish and serving size are sufficiently clear.",
        user_message="",
    )
