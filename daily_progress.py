"""Daily nutrition totals and progress formatting, shared by the 'today'/'undo'
commands and the meal-analysis reply."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from meal_logic import get_current_time_in_timezone
from models import Meal, NutritionEstimateRecord


@dataclass(frozen=True)
class DailyNutritionTotals:
    calories_min: float
    calories_max: float
    protein_min: float
    protein_max: float


def local_day_bounds_utc(local_now: datetime) -> tuple[datetime, datetime]:
    """Return the [start, end) of local_now's calendar day, in naive UTC
    (to match how SQLite stores timestamps)."""
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def get_daily_nutrition_totals(
    db: Session,
    user_phone: str,
    local_now: datetime | None = None,
) -> DailyNutritionTotals:
    """Sum analyzed meals within the user's current local calendar day."""
    local_now = local_now or get_current_time_in_timezone()
    start_utc, end_utc = local_day_bounds_utc(local_now)

    statement = (
        select(
            func.coalesce(func.sum(NutritionEstimateRecord.calories_min), 0.0),
            func.coalesce(func.sum(NutritionEstimateRecord.calories_max), 0.0),
            func.coalesce(func.sum(NutritionEstimateRecord.protein_min), 0.0),
            func.coalesce(func.sum(NutritionEstimateRecord.protein_max), 0.0),
        )
        .join(Meal, Meal.id == NutritionEstimateRecord.meal_id)
        .where(
            Meal.user_phone == user_phone,
            Meal.created_at >= start_utc,
            Meal.created_at < end_utc,
        )
    )
    row = db.execute(statement).one()

    return DailyNutritionTotals(
        calories_min=float(row[0]),
        calories_max=float(row[1]),
        protein_min=float(row[2]),
        protein_max=float(row[3]),
    )


def format_remaining(consumed_min: float, consumed_max: float, goal: float, unit: str) -> str:
    """Describe how much of `goal` is left, given a [consumed_min, consumed_max] range."""
    remaining_low = goal - consumed_max
    remaining_high = goal - consumed_min
    if remaining_high <= 0:
        return "goal reached"
    remaining_low = max(remaining_low, 0)
    if remaining_low == remaining_high:
        return f"{remaining_high:,.0f}{unit} left"
    return f"{remaining_low:,.0f}–{remaining_high:,.0f}{unit} left"


def format_daily_progress(
    totals: DailyNutritionTotals,
    calorie_goal: int,
    protein_goal: int,
) -> str:
    return (
        "Today's progress:\n"
        f"• Calories: {totals.calories_min:.0f}–"
        f"{totals.calories_max:.0f} / {calorie_goal:,} kcal "
        f"({format_remaining(totals.calories_min, totals.calories_max, calorie_goal, ' kcal')})\n"
        f"• Protein: {totals.protein_min:.0f}–"
        f"{totals.protein_max:.0f} / {protein_goal}g "
        f"({format_remaining(totals.protein_min, totals.protein_max, protein_goal, 'g')})"
    )
