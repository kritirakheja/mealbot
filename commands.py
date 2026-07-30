"""Non-image text commands available once a user is active: help, today,
undo, and resolving a pending clarification."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from daily_progress import (
    format_daily_progress,
    get_daily_nutrition_totals,
    local_day_bounds_utc,
)
from meal_logic import get_current_time_in_timezone
from models import Meal, NutritionEstimateRecord, User

PENDING_MEAL_STATUSES = {
    "needs_new_image",
    "awaiting_dish_choice",
    "awaiting_serving_size",
}

HELP_TEXT = (
    "Here's what I can do:\n"
    "• Send a photo of your meal to log it\n"
    "• today — see today's progress\n"
    "• undo — remove your last logged meal\n"
    "• goals — update your calorie/protein goals\n"
    "• reminders off / reminders on — mute or unmute meal reminders\n"
    "• reset — start over completely"
)


def get_pending_meal(db: Session, phone: str) -> Meal | None:
    """Return the most recent meal still waiting on a clarification, if any."""
    statement = (
        select(Meal)
        .where(Meal.user_phone == phone, Meal.status.in_(PENDING_MEAL_STATUSES))
        .order_by(Meal.created_at.desc())
    )
    return db.scalars(statement).first()


def build_today_summary(db: Session, phone: str) -> str:
    local_now = get_current_time_in_timezone()
    start_utc, end_utc = local_day_bounds_utc(local_now)

    statement = (
        select(Meal, NutritionEstimateRecord)
        .join(NutritionEstimateRecord, NutritionEstimateRecord.meal_id == Meal.id)
        .where(
            Meal.user_phone == phone,
            Meal.status == "analyzed",
            Meal.created_at >= start_utc,
            Meal.created_at < end_utc,
        )
        .order_by(Meal.created_at)
    )
    rows = db.execute(statement).all()

    if not rows:
        lines = ["No meals logged yet today. Send a photo whenever you eat!"]
    else:
        lines = ["Today so far:"]
        for meal, estimate in rows:
            foods = ", ".join(item["name"] for item in estimate.food_items) or "meal"
            meal_label = meal.meal_type.title() if meal.meal_type else "Meal"
            lines.append(
                f"• {meal_label} — {foods}: "
                f"{estimate.calories_min:.0f}–{estimate.calories_max:.0f} kcal"
            )

    user = db.get(User, phone)
    if user is not None and user.calorie_goal is not None and user.protein_goal is not None:
        totals = get_daily_nutrition_totals(db, phone, local_now)
        lines.append("")
        lines.append(format_daily_progress(totals, user.calorie_goal, user.protein_goal))

    return "\n".join(lines)


def undo_last_meal(db: Session, phone: str) -> str:
    local_now = get_current_time_in_timezone()
    start_utc, end_utc = local_day_bounds_utc(local_now)

    statement = (
        select(Meal)
        .where(
            Meal.user_phone == phone,
            Meal.status == "analyzed",
            Meal.created_at >= start_utc,
            Meal.created_at < end_utc,
        )
        .order_by(Meal.created_at.desc())
    )
    meal = db.scalars(statement).first()
    if meal is None:
        return "You don't have a meal logged today to undo."

    estimate = db.scalar(
        select(NutritionEstimateRecord).where(NutritionEstimateRecord.meal_id == meal.id)
    )
    food_names = (
        ", ".join(item["name"] for item in estimate.food_items)
        if estimate is not None and estimate.food_items
        else "that meal"
    )
    if estimate is not None:
        db.delete(estimate)
    meal.status = "undone"
    db.flush()

    message = f"Removed: {food_names}."
    user = db.get(User, phone)
    if user is not None and user.calorie_goal is not None and user.protein_goal is not None:
        totals = get_daily_nutrition_totals(db, phone, local_now)
        message += "\n\n" + format_daily_progress(totals, user.calorie_goal, user.protein_goal)
    return message
