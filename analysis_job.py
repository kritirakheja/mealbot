"""Background Gemini analysis of a stored meal photo, and startup recovery
for meals left mid-analysis by an interrupted process."""

import os
import time
from pathlib import Path

from sqlalchemy import select

from daily_progress import format_daily_progress, get_daily_nutrition_totals
from database import SessionLocal
from decision_policy import MealAction, decide_meal_action
from messaging import send_with_audit
from models import Meal, MealAnalysisAttempt, NutritionEstimateRecord, User
from nutrition import NutritionError, analyze_meal_image, format_nutrition_reply
from reminders import whatsapp_address

PENDING_STATUSES_BY_ACTION = {
    MealAction.REQUEST_NEW_IMAGE: "needs_new_image",
    MealAction.ASK_DISH_CHOICE: "awaiting_dish_choice",
    MealAction.ASK_SERVING_SIZE: "awaiting_serving_size",
}
ANALYSIS_RETRY_BACKOFF_SECONDS = (1, 3)


def recover_stuck_meals() -> int:
    """Recover meals left at status='stored' by an interrupted background
    task (e.g. a deploy or crash mid-analysis). A freshly started process
    can't have any in-flight BackgroundTask, so anything found here belongs
    to a previous run."""
    sender = os.getenv("TWILIO_WHATSAPP_FROM")
    if sender and not sender.startswith("whatsapp:"):
        sender = f"whatsapp:{sender}"

    db = SessionLocal()
    recovered = 0
    try:
        stuck_meals = db.scalars(select(Meal).where(Meal.status == "stored")).all()
        for meal in stuck_meals:
            meal.analysis_error = "Processing was interrupted by a server restart."
            meal.status = "analysis_failed"
            db.add(
                MealAnalysisAttempt(
                    meal_id=meal.id,
                    provider_error=meal.analysis_error,
                )
            )
            db.flush()
            if sender:
                send_with_audit(
                    db,
                    recipient=whatsapp_address(meal.user_phone),
                    sender=sender,
                    body=(
                        "Sorry — something interrupted processing your last "
                        "meal photo. Please resend it and I’ll try again."
                    ),
                    phone=meal.user_phone,
                    kind="stuck_meal_recovery",
                    meal_id=meal.id,
                )
            recovered += 1
        db.commit()
    finally:
        db.close()

    if recovered:
        print(f"[startup] Recovered {recovered} meal(s) stuck in 'stored'.")
    return recovered


def analyze_and_reply(
    meal_id: int,
    image_path: Path,
    image_content_type: str,
    recipient: str,
    sender: str,
) -> None:
    """Analyze a stored meal after the webhook response has been returned."""
    db = SessionLocal()

    try:
        meal = db.get(Meal, meal_id)
        if meal is None:
            print(f"[meal:{meal_id}] Meal no longer exists.")
            return

        print(f"[{meal.twilio_message_sid}] Calling Gemini...")

        analysis = None
        analysis_error: NutritionError | None = None
        for attempt, delay in enumerate(
            (0, *ANALYSIS_RETRY_BACKOFF_SECONDS), start=1
        ):
            if delay:
                time.sleep(delay)
            try:
                analysis = analyze_meal_image(image_path, image_content_type)
                analysis_error = None
                break
            except NutritionError as error:
                analysis_error = error
                print(
                    f"[{meal.twilio_message_sid}] Gemini attempt "
                    f"{attempt} failed: {error}"
                )

        if analysis_error is not None:
            meal.status = "analysis_failed"
            meal.analysis_error = str(analysis_error)
            db.add(
                MealAnalysisAttempt(
                    meal_id=meal.id,
                    provider_error=str(analysis_error),
                )
            )
            db.commit()

            print(f"[{meal.twilio_message_sid}] Nutrition error: {analysis_error}")
            message = (
                "Your meal photo was saved, but I couldn’t estimate "
                "its nutrition. Please try another photo."
            )
        else:
            decision = decide_meal_action(analysis)
            db.add(
                MealAnalysisAttempt(
                    meal_id=meal.id,
                    result=analysis.model_dump(mode="json"),
                    decision_action=decision.action.value,
                    decision_reason=decision.reason,
                )
            )

            meal.analysis_error = None

            if decision.action == MealAction.AUTO_LOG:
                estimate = analysis.estimate
                assert estimate is not None

                nutrition_record = NutritionEstimateRecord(
                    meal_id=meal.id,
                    food_items=[
                        item.model_dump(mode="json")
                        for item in estimate.food_items
                    ],
                    calories_min=estimate.total_calories_kcal.minimum,
                    calories_max=estimate.total_calories_kcal.maximum,
                    protein_min=estimate.total_protein_g.minimum,
                    protein_max=estimate.total_protein_g.maximum,
                    carbs_min=estimate.total_carbs_g.minimum,
                    carbs_max=estimate.total_carbs_g.maximum,
                    fat_min=estimate.total_fat_g.minimum,
                    fat_max=estimate.total_fat_g.maximum,
                    fibre_min=estimate.total_fibre_g.minimum,
                    fibre_max=estimate.total_fibre_g.maximum,
                    model_name="gemini-flash-latest",
                )

                db.add(nutrition_record)
                meal.status = "analyzed"

                # Make the new estimate visible to the aggregation query while
                # keeping the estimate and status in the same transaction.
                db.flush()
                user = db.get(User, meal.user_phone)
                totals = get_daily_nutrition_totals(db, meal.user_phone)

                db.commit()

                print(f"[{meal.twilio_message_sid}] Gemini analysis complete.")
                message = format_nutrition_reply(estimate, meal.meal_type)
                if (
                    user is not None
                    and user.calorie_goal is not None
                    and user.protein_goal is not None
                ):
                    message += "\n\n" + format_daily_progress(
                        totals,
                        user.calorie_goal,
                        user.protein_goal,
                    )
            else:
                meal.status = PENDING_STATUSES_BY_ACTION[decision.action]
                message = decision.user_message
                db.commit()

        outbound_message = send_with_audit(
            db,
            recipient=recipient,
            sender=sender,
            body=message,
            phone=meal.user_phone,
            kind="meal_analysis",
            meal_id=meal.id,
        )
        if outbound_message.status == "sent":
            print(f"[{meal.twilio_message_sid}] WhatsApp reply sent.")
        else:
            print(
                f"[{meal.twilio_message_sid}] "
                f"Could not send WhatsApp reply: {outbound_message.error}"
            )
    finally:
        db.close()
