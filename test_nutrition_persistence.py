from pathlib import Path

import analysis_job
import messaging
from database import Base
from models import (
    Meal,
    MealAnalysisAttempt,
    NutritionEstimateRecord,
    User,
)
from nutrition import (
    Confidence,
    FoodItem,
    ImageQuality,
    MealImageAnalysis,
    NutritionError,
    NutritionEstimate,
    NutritionRange,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


def make_test_session(tmp_path):
    database_path = tmp_path / "test_mealbot.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    return session_factory


def create_stored_meal(session_factory, message_sid):
    db = session_factory()
    user = User(
        phone="919999999999",
        onboarding_state="active",
        calorie_goal=2000,
        protein_goal=120,
    )
    db.add(user)

    meal = Meal(
        user_phone=user.phone,
        twilio_message_sid=message_sid,
        image_url="https://example.com/test.jpg",
        image_content_type="image/jpeg",
        meal_type="lunch",
        status="stored",
    )
    db.add(meal)
    db.commit()

    meal_id = meal.id
    db.close()
    return meal_id


def build_estimate():
    return NutritionEstimate(
        food_items=[
            FoodItem(
                name="dal and rice",
                portion_description="one medium plate",
                calories_kcal=NutritionRange(minimum=450, maximum=600),
                protein_g=NutritionRange(minimum=15, maximum=22),
                carbs_g=NutritionRange(minimum=70, maximum=90),
                fat_g=NutritionRange(minimum=10, maximum=18),
                fibre_g=NutritionRange(minimum=8, maximum=12),
            )
        ],
        total_calories_kcal=NutritionRange(minimum=450, maximum=600),
        total_protein_g=NutritionRange(minimum=15, maximum=22),
        total_carbs_g=NutritionRange(minimum=70, maximum=90),
        total_fat_g=NutritionRange(minimum=10, maximum=18),
        total_fibre_g=NutritionRange(minimum=8, maximum=12),
    )


def build_analysis():
    return MealImageAnalysis(
        image_quality=ImageQuality.CLEAR,
        visible_foods=["dal", "rice"],
        dish_candidates=["dal and rice"],
        dish_confidence=Confidence.HIGH,
        portion_confidence=Confidence.HIGH,
        assumptions=["The plate is a standard dinner plate."],
        estimate=build_estimate(),
    )


def test_nutrition_result_persists_without_external_calls(
    monkeypatch,
    tmp_path,
):
    session_factory = make_test_session(tmp_path)
    meal_id = create_stored_meal(session_factory, "SM_SUCCESS")
    sent_messages = []

    monkeypatch.setattr(analysis_job, "SessionLocal", session_factory)
    monkeypatch.setattr(
        analysis_job,
        "analyze_meal_image",
        lambda image_path, mime_type: build_analysis(),
    )
    monkeypatch.setattr(
        messaging,
        "send_whatsapp_message",
        lambda recipient, sender, message: sent_messages.append(message),
    )

    analysis_job.analyze_and_reply(
        meal_id=meal_id,
        image_path=Path("unused-test-image.jpg"),
        image_content_type="image/jpeg",
        recipient="whatsapp:+919999999999",
        sender="whatsapp:+14155238886",
    )

    verification_db = session_factory()
    saved_meal = verification_db.get(Meal, meal_id)
    saved_estimate = verification_db.scalar(
        select(NutritionEstimateRecord).where(
            NutritionEstimateRecord.meal_id == meal_id
        )
    )
    saved_attempt = verification_db.scalar(
        select(MealAnalysisAttempt).where(
            MealAnalysisAttempt.meal_id == meal_id
        )
    )

    assert saved_meal.status == "analyzed"
    assert saved_meal.analysis_error is None
    assert saved_estimate is not None
    assert saved_estimate.calories_min == 450
    assert saved_estimate.calories_max == 600
    assert saved_estimate.protein_min == 15
    assert saved_estimate.protein_max == 22
    assert saved_estimate.model_name == "gemini-flash-latest"
    assert saved_estimate.food_items[0]["name"] == "dal and rice"
    assert saved_attempt is not None
    assert saved_attempt.decision_action == "auto_log"
    assert saved_attempt.result["assumptions"] == [
        "The plate is a standard dinner plate."
    ]
    assert len(sent_messages) == 1
    assert "Today's progress:" in sent_messages[0]
    assert "Calories: 450–600 / 2,000 kcal" in sent_messages[0]
    assert "Protein: 15–22 / 120g" in sent_messages[0]
    verification_db.close()


def test_nutrition_failure_persists_without_external_calls(
    monkeypatch,
    tmp_path,
):
    session_factory = make_test_session(tmp_path)
    meal_id = create_stored_meal(session_factory, "SM_FAILURE")
    sent_messages = []

    def fail_analysis(image_path, mime_type):
        raise NutritionError("test analysis failure")

    monkeypatch.setattr(analysis_job.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(analysis_job, "SessionLocal", session_factory)
    monkeypatch.setattr(analysis_job, "analyze_meal_image", fail_analysis)
    monkeypatch.setattr(
        messaging,
        "send_whatsapp_message",
        lambda recipient, sender, message: sent_messages.append(message),
    )

    analysis_job.analyze_and_reply(
        meal_id=meal_id,
        image_path=Path("unused-test-image.jpg"),
        image_content_type="image/jpeg",
        recipient="whatsapp:+919999999999",
        sender="whatsapp:+14155238886",
    )

    verification_db = session_factory()
    saved_meal = verification_db.get(Meal, meal_id)
    saved_estimate = verification_db.scalar(
        select(NutritionEstimateRecord).where(
            NutritionEstimateRecord.meal_id == meal_id
        )
    )
    saved_attempt = verification_db.scalar(
        select(MealAnalysisAttempt).where(
            MealAnalysisAttempt.meal_id == meal_id
        )
    )

    assert saved_meal.status == "analysis_failed"
    assert saved_meal.analysis_error == "test analysis failure"
    assert saved_estimate is None
    assert saved_attempt is not None
    assert saved_attempt.provider_error == "test analysis failure"
    assert len(sent_messages) == 1
    verification_db.close()


def test_unusable_image_is_audited_without_nutrition_log(
    monkeypatch,
    tmp_path,
):
    session_factory = make_test_session(tmp_path)
    meal_id = create_stored_meal(session_factory, "SM_BLURRY")
    sent_messages = []
    blurry_analysis = MealImageAnalysis(
        image_quality=ImageQuality.UNUSABLE,
        visible_foods=[],
        dish_candidates=[],
        dish_confidence=Confidence.LOW,
        portion_confidence=Confidence.LOW,
        assumptions=[],
        ambiguity_reason="The photo is heavily blurred.",
        estimate=None,
    )

    monkeypatch.setattr(analysis_job, "SessionLocal", session_factory)
    monkeypatch.setattr(
        analysis_job,
        "analyze_meal_image",
        lambda image_path, mime_type: blurry_analysis,
    )
    monkeypatch.setattr(
        messaging,
        "send_whatsapp_message",
        lambda recipient, sender, message: sent_messages.append(message),
    )

    analysis_job.analyze_and_reply(
        meal_id=meal_id,
        image_path=Path("unused-test-image.jpg"),
        image_content_type="image/jpeg",
        recipient="whatsapp:+919999999999",
        sender="whatsapp:+14155238886",
    )

    verification_db = session_factory()
    saved_meal = verification_db.get(Meal, meal_id)
    saved_estimate = verification_db.scalar(
        select(NutritionEstimateRecord).where(
            NutritionEstimateRecord.meal_id == meal_id
        )
    )
    saved_attempt = verification_db.scalar(
        select(MealAnalysisAttempt).where(
            MealAnalysisAttempt.meal_id == meal_id
        )
    )

    assert saved_meal.status == "needs_new_image"
    assert saved_estimate is None
    assert saved_attempt is not None
    assert saved_attempt.decision_action == "request_new_image"
    assert len(sent_messages) == 1
    assert "haven’t logged" in sent_messages[0]
    verification_db.close()


def test_analysis_retries_transient_failure_then_succeeds(
    monkeypatch,
    tmp_path,
):
    """A transient Gemini failure on the first attempt should not fail the
    meal outright — analyze_and_reply retries before giving up."""
    session_factory = make_test_session(tmp_path)
    meal_id = create_stored_meal(session_factory, "SM_RETRY_SUCCESS")
    sent_messages = []
    call_count = 0

    def flaky_analysis(image_path, mime_type):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise NutritionError("transient network error")
        return build_analysis()

    monkeypatch.setattr(analysis_job.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(analysis_job, "SessionLocal", session_factory)
    monkeypatch.setattr(analysis_job, "analyze_meal_image", flaky_analysis)
    monkeypatch.setattr(
        messaging,
        "send_whatsapp_message",
        lambda recipient, sender, message: sent_messages.append(message),
    )

    analysis_job.analyze_and_reply(
        meal_id=meal_id,
        image_path=Path("unused-test-image.jpg"),
        image_content_type="image/jpeg",
        recipient="whatsapp:+919999999999",
        sender="whatsapp:+14155238886",
    )

    verification_db = session_factory()
    saved_meal = verification_db.get(Meal, meal_id)
    saved_attempt = verification_db.scalar(
        select(MealAnalysisAttempt).where(
            MealAnalysisAttempt.meal_id == meal_id
        )
    )

    assert call_count == 2
    assert saved_meal.status == "analyzed"
    assert saved_attempt is not None
    assert saved_attempt.decision_action == "auto_log"
    assert len(sent_messages) == 1
    verification_db.close()


def test_recover_stuck_meals_marks_failed_and_notifies(monkeypatch, tmp_path):
    """A meal left at status='stored' by an interrupted background task
    (e.g. a restart) should be recovered at startup, not silently stuck."""
    session_factory = make_test_session(tmp_path)
    meal_id = create_stored_meal(session_factory, "SM_STUCK")
    sent_messages = []

    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setattr(analysis_job, "SessionLocal", session_factory)
    monkeypatch.setattr(
        messaging,
        "send_whatsapp_message",
        lambda recipient, sender, message: sent_messages.append(
            (recipient, message)
        ),
    )

    recovered = analysis_job.recover_stuck_meals()

    verification_db = session_factory()
    saved_meal = verification_db.get(Meal, meal_id)
    saved_attempt = verification_db.scalar(
        select(MealAnalysisAttempt).where(
            MealAnalysisAttempt.meal_id == meal_id
        )
    )

    assert recovered == 1
    assert saved_meal.status == "analysis_failed"
    assert "interrupted" in saved_meal.analysis_error
    assert saved_attempt is not None
    assert len(sent_messages) == 1
    assert sent_messages[0][0] == "whatsapp:+919999999999"
    verification_db.close()
