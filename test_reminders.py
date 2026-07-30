from datetime import datetime, time, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import reminders
from database import Base
from models import Meal, User
from reminders import MEAL_REMINDERS, MealReminder, next_run_at, whatsapp_address


IST = ZoneInfo("Asia/Kolkata")


def make_session(tmp_path):
    database_path = tmp_path / "test_reminders.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_meal_reminder_schedule():
    assert [(item.meal, item.at) for item in MEAL_REMINDERS] == [
        ("breakfast", time(10, 0)),
        ("lunch", time(14, 0)),
        ("dinner", time(20, 30)),
    ]


def test_next_run_uses_today_when_time_is_still_ahead():
    now = datetime(2026, 7, 24, 9, 30, tzinfo=IST)
    assert next_run_at(time(10, 0), now) == datetime(
        2026, 7, 24, 10, 0, tzinfo=IST
    )


def test_next_run_moves_to_tomorrow_after_time_has_passed():
    now = datetime(2026, 7, 24, 14, 1, tzinfo=IST)
    assert next_run_at(time(14, 0), now) == datetime(
        2026, 7, 25, 14, 0, tzinfo=IST
    )


def test_whatsapp_address_uses_twilio_format():
    assert whatsapp_address("919999999999") == "whatsapp:+919999999999"


def to_naive_utc(local_dt: datetime) -> datetime:
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def make_meal(phone: str, meal_type: str, local_created_at: datetime, sid: str) -> Meal:
    return Meal(
        user_phone=phone,
        twilio_message_sid=sid,
        image_url="https://example.com/image.jpg",
        meal_type=meal_type,
        status="stored",
        created_at=to_naive_utc(local_created_at),
    )


def test_phones_with_logged_meal_matches_same_day_meal_type(tmp_path):
    db = make_session(tmp_path)
    db.add(User(phone="919999999999", onboarding_state="active"))
    db.add_all(
        [
            make_meal(
                "919999999999", "dinner", datetime(2026, 7, 24, 19, 30, tzinfo=IST), "SM1"
            ),
            make_meal(
                "919999999999", "lunch", datetime(2026, 7, 24, 13, 0, tzinfo=IST), "SM2"
            ),
            make_meal(
                "919999999999", "dinner", datetime(2026, 7, 23, 20, 0, tzinfo=IST), "SM3"
            ),
        ]
    )
    db.commit()

    now = datetime(2026, 7, 24, 20, 30, tzinfo=IST)
    assert reminders.phones_with_logged_meal(db, "dinner", now) == {"919999999999"}
    assert reminders.phones_with_logged_meal(db, "breakfast", now) == set()
    db.close()


def test_send_reminder_skips_users_who_already_logged(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_send_reminder.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    db = session_factory()
    db.add_all(
        [
            User(phone="919999999999", onboarding_state="active", reminders_enabled=True),
            User(phone="918888888888", onboarding_state="active", reminders_enabled=True),
        ]
    )
    db.add(
        make_meal(
            "919999999999", "dinner", datetime(2026, 7, 24, 19, 30, tzinfo=IST), "SM1"
        )
    )
    db.commit()
    db.close()

    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setattr(reminders, "SessionLocal", session_factory)

    messaged = []

    def fake_send_with_audit(db, *, recipient, sender, body, phone, kind, meal_id=None):
        messaged.append(phone)
        return SimpleNamespace(status="sent", error=None)

    monkeypatch.setattr(reminders, "send_with_audit", fake_send_with_audit)

    reminder = MealReminder("dinner", time(20, 30), "It's 8:30 p.m. — time for dinner!")
    now = datetime(2026, 7, 24, 20, 30, tzinfo=IST)

    sent = reminders.send_reminder(reminder, now=now)

    assert sent == 1
    assert messaged == ["918888888888"]
