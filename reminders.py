"""Daily WhatsApp meal reminders."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from conversation import ACTIVE
from database import SessionLocal
from messaging import send_whatsapp_message
from models import User

REMINDER_TIMEZONE = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class MealReminder:
    meal: str
    at: time
    message: str


MEAL_REMINDERS = (
    MealReminder("breakfast", time(10, 0), "Good morning! It’s time for breakfast."),
    MealReminder("lunch", time(14, 0), "It’s 2 p.m. — time for lunch!"),
    MealReminder("dinner", time(20, 30), "It’s 8:30 p.m. — time for dinner!"),
)


def whatsapp_address(phone: str) -> str:
    """Convert a stored normalized phone number to a Twilio WhatsApp address."""
    return f"whatsapp:+{phone}"


def next_run_at(at: time, now: datetime | None = None) -> datetime:
    """Return the next occurrence of a local wall-clock reminder time."""
    local_now = now or datetime.now(REMINDER_TIMEZONE)
    candidate = datetime.combine(local_now.date(), at, REMINDER_TIMEZONE)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def send_reminder(reminder: MealReminder) -> int:
    """Send one reminder to every active user and return the success count."""
    sender = os.getenv("TWILIO_WHATSAPP_FROM")
    if not sender:
        print(
            f"[reminder:{reminder.meal}] Skipped: "
            "TWILIO_WHATSAPP_FROM is not configured."
        )
        return 0

    if not sender.startswith("whatsapp:"):
        sender = f"whatsapp:{sender}"

    db = SessionLocal()
    sent = 0
    try:
        active_phones = db.scalars(
            select(User.phone).where(User.onboarding_state == ACTIVE)
        ).all()
        for phone in active_phones:
            try:
                send_whatsapp_message(
                    whatsapp_address(phone),
                    sender,
                    reminder.message,
                )
                sent += 1
            except Exception as error:
                print(f"[reminder:{reminder.meal}] Could not message {phone}: {error}")
    finally:
        db.close()

    print(f"[reminder:{reminder.meal}] Sent to {sent} active user(s).")
    return sent


async def run_daily_reminder(reminder: MealReminder) -> None:
    """Wait for and send a reminder at the configured local time every day."""
    while True:
        delay = (next_run_at(reminder.at) - datetime.now(REMINDER_TIMEZONE)).total_seconds()
        await asyncio.sleep(max(delay, 0))
        await asyncio.to_thread(send_reminder, reminder)


def start_reminder_tasks() -> list[asyncio.Task[None]]:
    return [
        asyncio.create_task(
            run_daily_reminder(reminder),
            name=f"{reminder.meal}-reminder",
        )
        for reminder in MEAL_REMINDERS
    ]
