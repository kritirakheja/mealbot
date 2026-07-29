"""Onboarding conversation logic. No FastAPI, no Twilio, no database calls."""

from models import User

# onboarding states
NEW = "new"
AWAITING_CALORIE_GOAL = "awaiting_calorie_goal"
AWAITING_PROTEIN_GOAL = "awaiting_protein_goal"
ACTIVE = "active"


def normalize_phone(raw: str) -> str:
    """Turn 'whatsapp:+919999999999' into '919999999999'."""
    return raw.replace("whatsapp:", "").replace("+", "").strip()


def parse_number(text: str) -> int | None:
    """Parse a user-typed number. Returns None if it isn't one."""
    try:
        number =  text.replace(",", "")
        if number.isdigit() and int(number) > 0:
            return int(number)
        return
    except ValueError:
        return None


def handle_message(user: User, body: str) -> str:
    """Update the user in place based on their message. Return the reply text."""
    text = body.strip().lower()

    if text == "reset":
        user.onboarding_state = NEW
        user.calorie_goal = None
        user.protein_goal = None
        return "Reset. Send 'start' to begin again."

    if text == "start":
        user.onboarding_state = AWAITING_CALORIE_GOAL
        return "Welcome! What is your daily calorie goal?"

    if user.onboarding_state == AWAITING_CALORIE_GOAL:
        value = parse_number(text)
        if value is None:
            return "Please send a number, like 1800."
        user.calorie_goal = value
        user.onboarding_state = AWAITING_PROTEIN_GOAL
        return "Great. What is your daily protein goal in grams?"

    if user.onboarding_state == AWAITING_PROTEIN_GOAL:
        value = parse_number(text)
        if value is None:
            return "Please send a number, like 100."
        user.protein_goal = value
        user.onboarding_state = ACTIVE
        return (
            f"You're set: {user.calorie_goal:,} kcal and {user.protein_goal}g protein daily.\n"
            "Send a meal photo whenever you eat."
        )

    return "Send 'start' to begin."
