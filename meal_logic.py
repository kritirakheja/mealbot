from datetime import datetime, time
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Kolkata"


def get_current_time_in_timezone(
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))


def infer_meal_type(at_time: time) -> str:
    """Infer a meal type from a user's local clock time."""
    if time(5, 0) <= at_time < time(11, 0):
        return "breakfast"

    if time(11, 0) <= at_time < time(16, 0):
        return "lunch"

    if time(16, 0) <= at_time < time(18, 0):
        return "snack"

    return "dinner"
