"""Daily WhatsApp meal reminders."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from conversation import ACTIVE
from daily_progress import local_day_bounds_utc
from database import SessionLocal
from messaging import send_with_audit
from models import Meal, User

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


def phones_with_logged_meal(db: Session, meal_type: str, local_now: datetime) -> set[str]:
    """Phones that already have a Meal of this type submitted today (local calendar day)."""
    start_utc, end_utc = local_day_bounds_utc(local_now)
    return set(
        db.scalars(
            select(Meal.user_phone)
            .where(
                Meal.meal_type == meal_type,
                Meal.created_at >= start_utc,
                Meal.created_at < end_utc,
            )
            .distinct()
        ).all()
    )


def send_reminder(reminder: MealReminder, now: datetime | None = None) -> int:
    """Send one reminder to every active user who hasn't already logged that
    meal today, and return the success count."""
    sender = os.getenv("TWILIO_WHATSAPP_FROM")
    if not sender:
        print(
            f"[reminder:{reminder.meal}] Skipped: "
            "TWILIO_WHATSAPP_FROM is not configured."
        )
        return 0

    if not sender.startswith("whatsapp:"):
        sender = f"whatsapp:{sender}"

    now = now or datetime.now(REMINDER_TIMEZONE)

    db = SessionLocal()
    sent = 0
    skipped = 0
    try:
        active_phones = db.scalars(
            select(User.phone).where(
                User.onboarding_state == ACTIVE,
                User.reminders_enabled == True,  # noqa: E712 (SQLAlchemy needs `== True`, not `is True`)
            )
        ).all()
        already_logged = phones_with_logged_meal(db, reminder.meal, now)
        for phone in active_phones:
            if phone in already_logged:
                skipped += 1
                continue
            outbound_message = send_with_audit(
                db,
                recipient=whatsapp_address(phone),
                sender=sender,
                body=reminder.message,
                phone=phone,
                kind="reminder",
            )
            if outbound_message.status == "sent":
                sent += 1
            else:
                print(
                    f"[reminder:{reminder.meal}] Could not message "
                    f"{phone}: {outbound_message.error}"
                )
    finally:
        db.close()

    print(
        f"[reminder:{reminder.meal}] Sent to {sent} active user(s), "
        f"skipped {skipped} already logged."
    )
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
