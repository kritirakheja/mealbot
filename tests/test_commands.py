from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import main
import messaging
import reminders
from database import Base, get_db
from models import Meal, NutritionEstimateRecord, User


def make_client(tmp_path, monkeypatch):
    database_path = tmp_path / "test_commands.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(
        main.app, "dependency_overrides", {get_db: override_get_db}
    )
    monkeypatch.setattr(main, "validate_twilio_signature", lambda request, form: True)

    client = TestClient(main.app)
    return client, session_factory


def post_message(client, *, from_number, body, message_sid, num_media=0):
    return client.post(
        "/whatsapp",
        data={
            "From": from_number,
            "To": "whatsapp:+14155238886",
            "Body": body,
            "MessageSid": message_sid,
            "NumMedia": str(num_media),
        },
    )


def add_active_user(session_factory, phone, calorie_goal=2000, protein_goal=120):
    db = session_factory()
    db.add(
        User(
            phone=phone,
            onboarding_state="active",
            calorie_goal=calorie_goal,
            protein_goal=protein_goal,
        )
    )
    db.commit()
    db.close()


def add_analyzed_meal(
    session_factory,
    *,
    phone,
    message_sid,
    food_name,
    calories,
    protein,
    meal_type="lunch",
    created_at=None,
):
    db = session_factory()
    meal = Meal(
        user_phone=phone,
        twilio_message_sid=message_sid,
        image_url="https://example.com/test.jpg",
        image_content_type="image/jpeg",
        meal_type=meal_type,
        status="analyzed",
        created_at=created_at or datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(meal)
    db.flush()
    db.add(
        NutritionEstimateRecord(
            meal_id=meal.id,
            food_items=[{"name": food_name}],
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
    db.commit()
    db.close()


def test_today_with_no_meals_logged(tmp_path, monkeypatch):
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919000000001"
    add_active_user(session_factory, phone)

    response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="today", message_sid="SID_TODAY_EMPTY"
    )

    assert "No meals logged yet today" in response.text


def test_today_lists_logged_meals_and_progress(tmp_path, monkeypatch):
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919000000002"
    add_active_user(session_factory, phone, calorie_goal=2000, protein_goal=120)
    add_analyzed_meal(
        session_factory,
        phone=phone,
        message_sid="SID_MEAL_1",
        food_name="dal and rice",
        calories=(450, 600),
        protein=(15, 22),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="today", message_sid="SID_TODAY_1"
    )

    assert "dal and rice" in response.text
    assert "Today's progress:" in response.text


def test_undo_removes_last_meal_from_totals(tmp_path, monkeypatch):
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919000000003"
    add_active_user(session_factory, phone, calorie_goal=2000, protein_goal=120)
    add_analyzed_meal(
        session_factory,
        phone=phone,
        message_sid="SID_MEAL_UNDO",
        food_name="fried rice",
        calories=(500, 650),
        protein=(10, 18),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    undo_response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="undo", message_sid="SID_UNDO"
    )
    assert "Removed: fried rice" in undo_response.text

    db = session_factory()
    meal = db.scalar(select(Meal).where(Meal.twilio_message_sid == "SID_MEAL_UNDO"))
    estimate = db.scalar(
        select(NutritionEstimateRecord).where(NutritionEstimateRecord.meal_id == meal.id)
    )
    db.close()
    assert meal.status == "undone"
    assert estimate is None

    today_response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="today", message_sid="SID_TODAY_AFTER_UNDO"
    )
    assert "No meals logged yet today" in today_response.text


def test_undo_with_nothing_to_undo(tmp_path, monkeypatch):
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919000000004"
    add_active_user(session_factory, phone)

    response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="undo", message_sid="SID_UNDO_EMPTY"
    )

    assert "don't have a meal logged today" in response.text


def test_reset_works_from_active_user(tmp_path, monkeypatch):
    """Regression test: 'reset' used to be silently ignored once a user was
    ACTIVE, because handle_message was never called in that state."""
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919000000005"
    add_active_user(session_factory, phone)

    response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="reset", message_sid="SID_RESET"
    )

    assert "Reset" in response.text

    db = session_factory()
    user = db.get(User, phone)
    db.close()
    assert user.onboarding_state == "new"
    assert user.calorie_goal is None


def test_help_while_active_shows_command_menu(tmp_path, monkeypatch):
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919000000006"
    add_active_user(session_factory, phone)

    response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="help", message_sid="SID_HELP"
    )

    assert "today" in response.text
    assert "undo" in response.text
    assert "reminders off" in response.text


def test_reminders_off_excludes_user_from_send_reminder(tmp_path, monkeypatch):
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919000000007"
    add_active_user(session_factory, phone)

    response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="reminders off", message_sid="SID_MUTE"
    )
    assert "paused" in response.text.lower()

    sent_messages = []
    monkeypatch.setattr(reminders, "SessionLocal", session_factory)
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setattr(
        messaging,
        "send_whatsapp_message",
        lambda recipient, sender, message: sent_messages.append(recipient),
    )

    sent_count = reminders.send_reminder(reminders.MEAL_REMINDERS[0])

    assert sent_count == 0
    assert sent_messages == []

    unmute_response = post_message(
        client, from_number=f"whatsapp:+{phone}", body="reminders on", message_sid="SID_UNMUTE"
    )
    assert "back on" in unmute_response.text.lower()

    sent_count_after = reminders.send_reminder(reminders.MEAL_REMINDERS[0])
    assert sent_count_after == 1
    assert sent_messages == [f"whatsapp:+{phone}"]
