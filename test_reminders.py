from datetime import datetime, time
from zoneinfo import ZoneInfo

from reminders import MEAL_REMINDERS, next_run_at, whatsapp_address


IST = ZoneInfo("Asia/Kolkata")


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
