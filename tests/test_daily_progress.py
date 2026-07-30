from datetime import datetime
from zoneinfo import ZoneInfo

from daily_progress import (
    DailyNutritionTotals,
    format_daily_progress,
    get_daily_nutrition_totals,
)
from database import Base
from models import Meal, NutritionEstimateRecord, User
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def make_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-progress.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_meal_with_estimate(
    db,
    *,
    phone,
    message_sid,
    created_at,
    calories,
    protein,
):
    meal = Meal(
        user_phone=phone,
        twilio_message_sid=message_sid,
        image_url="https://example.com/meal.jpg",
        image_content_type="image/jpeg",
        meal_type="lunch",
        status="analyzed",
        created_at=created_at,
    )
    db.add(meal)
    db.flush()
    db.add(
        NutritionEstimateRecord(
            meal_id=meal.id,
            food_items=[],
            calories_min=calories[0],
            calories_max=calories[1],
            protein_min=protein[0],
            protein_max=protein[1],
            carbs_min=0,
            carbs_max=0,
            fat_min=0,
            fat_max=0,
            fibre_min=0,
            fibre_max=0,
            model_name="test-model",
        )
    )


def test_daily_totals_include_only_current_day_and_user(tmp_path):
    db = make_session(tmp_path)
    db.add_all(
        [
            User(phone="111", calorie_goal=2000, protein_goal=100),
            User(phone="222", calorie_goal=1800, protein_goal=90),
        ]
    )
    db.flush()

    # The tested local day is July 23 in Asia/Kolkata, whose UTC boundaries
    # are July 22 at 18:30 through July 23 at 18:30.
    add_meal_with_estimate(
        db,
        phone="111",
        message_sid="SM_TODAY_1",
        created_at=datetime(2026, 7, 22, 19, 0),
        calories=(400, 500),
        protein=(20, 25),
    )
    add_meal_with_estimate(
        db,
        phone="111",
        message_sid="SM_TODAY_2",
        created_at=datetime(2026, 7, 23, 12, 0),
        calories=(300, 450),
        protein=(15, 22),
    )
    add_meal_with_estimate(
        db,
        phone="111",
        message_sid="SM_YESTERDAY",
        created_at=datetime(2026, 7, 22, 18, 0),
        calories=(900, 1000),
        protein=(40, 50),
    )
    add_meal_with_estimate(
        db,
        phone="222",
        message_sid="SM_OTHER_USER",
        created_at=datetime(2026, 7, 23, 12, 0),
        calories=(800, 900),
        protein=(35, 45),
    )
    db.commit()

    totals = get_daily_nutrition_totals(
        db,
        "111",
        datetime(2026, 7, 23, 20, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert totals == DailyNutritionTotals(
        calories_min=700,
        calories_max=950,
        protein_min=35,
        protein_max=47,
    )
    db.close()


def test_daily_progress_formats_ranges_and_goal_percentages():
    message = format_daily_progress(
        DailyNutritionTotals(
            calories_min=700,
            calories_max=950,
            protein_min=35,
            protein_max=47,
        ),
        calorie_goal=2000,
        protein_goal=100,
    )

    assert "Calories: 700–950 / 2,000 kcal (1,050–1,300 kcal left)" in message
    assert "Protein: 35–47 / 100g (53–65g left)" in message
