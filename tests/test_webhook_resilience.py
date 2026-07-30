from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import main
from database import Base, get_db
from models import InboundMessage, Meal, User


def make_client(tmp_path, monkeypatch):
    database_path = tmp_path / "test_webhook.db"
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


def test_duplicate_message_sid_is_not_reprocessed(tmp_path, monkeypatch):
    """A retried Twilio webhook (same MessageSid) must not be able to
    reinterpret its Body against whatever state the user has since moved
    to — it should just replay the original reply."""
    client, session_factory = make_client(tmp_path, monkeypatch)
    from_number = "whatsapp:+919999999999"

    post_message(client, from_number=from_number, body="start", message_sid="SID_START")
    calorie_response = post_message(
        client, from_number=from_number, body="1800", message_sid="SID_CALORIE"
    )

    db = session_factory()
    user = db.get(User, "919999999999")
    assert user.onboarding_state == "awaiting_protein_goal"
    assert user.calorie_goal == 1800
    assert user.protein_goal is None
    db.close()

    retried_response = post_message(
        client, from_number=from_number, body="1800", message_sid="SID_CALORIE"
    )

    assert retried_response.text == calorie_response.text

    db = session_factory()
    user = db.get(User, "919999999999")
    inbound_count = len(
        db.scalars(
            select(InboundMessage).where(InboundMessage.message_sid == "SID_CALORIE")
        ).all()
    )
    db.close()

    # The retry must not have reinterpreted "1800" as the protein goal.
    assert user.onboarding_state == "awaiting_protein_goal"
    assert user.protein_goal is None
    assert inbound_count == 1


def test_reply_to_pending_meal_closes_it_with_clarification(tmp_path, monkeypatch):
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919999999999"

    db = session_factory()
    db.add(
        User(
            phone=phone,
            onboarding_state="active",
            calorie_goal=2000,
            protein_goal=120,
        )
    )
    db.add(
        Meal(
            user_phone=phone,
            twilio_message_sid="SID_IMAGE",
            image_url="https://example.com/test.jpg",
            image_content_type="image/jpeg",
            meal_type="lunch",
            status="awaiting_dish_choice",
        )
    )
    db.commit()
    db.close()

    response = post_message(
        client,
        from_number=f"whatsapp:+{phone}",
        body="chicken curry",
        message_sid="SID_CLARIFY",
    )

    assert "fresh photo" in response.text

    db = session_factory()
    meal = db.scalar(select(Meal).where(Meal.twilio_message_sid == "SID_IMAGE"))
    db.close()

    assert meal.status == "clarification_closed"
    assert meal.clarification_text == "chicken curry"


def test_text_without_pending_meal_asks_for_image(tmp_path, monkeypatch):
    client, session_factory = make_client(tmp_path, monkeypatch)
    phone = "919999999999"

    db = session_factory()
    db.add(
        User(
            phone=phone,
            onboarding_state="active",
            calorie_goal=2000,
            protein_goal=120,
        )
    )
    db.commit()
    db.close()

    response = post_message(
        client,
        from_number=f"whatsapp:+{phone}",
        body="hi",
        message_sid="SID_RANDOM_TEXT",
    )

    assert "Please send an image of your meal." in response.text
