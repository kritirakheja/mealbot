import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from database import Base, SessionLocal, engine, get_db
from models import Meal, NutritionEstimate as NutritionEstimateRecord, User

from conversation import ACTIVE, handle_message, normalize_phone
from meal_logic import get_current_time_in_timezone, infer_meal_type
from messaging import send_whatsapp_message
from reminders import start_reminder_tasks

from storage import StorageError, download_and_save_image

from nutrition import (
    NutritionError,
    analyze_meal_image,
    format_nutrition_reply,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    reminder_tasks = start_reminder_tasks()
    try:
        yield
    finally:
        for task in reminder_tasks:
            task.cancel()
        await asyncio.gather(*reminder_tasks, return_exceptions=True)

app = FastAPI(lifespan=lifespan)

# helper functions


@dataclass(frozen=True)
class DailyNutritionTotals:
    calories_min: float
    calories_max: float
    protein_min: float
    protein_max: float


def get_or_create(db: Session, phone: str) -> User:
    user = db.get(User, phone)
    if user is None:
        user = User(phone=phone)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_meal_by_message_sid(
    db: Session, message_sid: str
) -> Meal | None:
    statement = select(Meal).where(
        Meal.twilio_message_sid == message_sid
    )
    return db.scalar(statement)


def get_daily_nutrition_totals(
    db: Session,
    user_phone: str,
    local_now: datetime | None = None,
) -> DailyNutritionTotals:
    """Sum analyzed meals within the user's current local calendar day."""
    local_now = local_now or get_current_time_in_timezone()
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)

    # SQLite stores these timestamps as naive UTC values.
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)

    statement = (
        select(
            func.coalesce(func.sum(NutritionEstimateRecord.calories_min), 0.0),
            func.coalesce(func.sum(NutritionEstimateRecord.calories_max), 0.0),
            func.coalesce(func.sum(NutritionEstimateRecord.protein_min), 0.0),
            func.coalesce(func.sum(NutritionEstimateRecord.protein_max), 0.0),
        )
        .join(Meal, Meal.id == NutritionEstimateRecord.meal_id)
        .where(
            Meal.user_phone == user_phone,
            Meal.created_at >= start_utc,
            Meal.created_at < end_utc,
        )
    )
    row = db.execute(statement).one()

    return DailyNutritionTotals(
        calories_min=float(row[0]),
        calories_max=float(row[1]),
        protein_min=float(row[2]),
        protein_max=float(row[3]),
    )


def format_daily_progress(
    totals: DailyNutritionTotals,
    calorie_goal: int,
    protein_goal: int,
) -> str:
    calorie_min_percent = totals.calories_min / calorie_goal * 100
    calorie_max_percent = totals.calories_max / calorie_goal * 100
    protein_min_percent = totals.protein_min / protein_goal * 100
    protein_max_percent = totals.protein_max / protein_goal * 100

    return (
        "Today's progress:\n"
        f"• Calories: {totals.calories_min:.0f}–"
        f"{totals.calories_max:.0f} / {calorie_goal:,} kcal "
        f"({calorie_min_percent:.0f}–{calorie_max_percent:.0f}%)\n"
        f"• Protein: {totals.protein_min:.0f}–"
        f"{totals.protein_max:.0f} / {protein_goal}g "
        f"({protein_min_percent:.0f}–{protein_max_percent:.0f}%)"
    )


def analyze_and_reply(
    meal_id: int,
    image_path: Path,
    image_content_type: str,
    recipient: str,
    sender: str,
) -> None:
    """Analyze a stored meal after the webhook response has been returned."""
    db = SessionLocal()

    try:
        meal = db.get(Meal, meal_id)
        if meal is None:
            print(f"[meal:{meal_id}] Meal no longer exists.")
            return

        print(f"[{meal.twilio_message_sid}] Calling Gemini...")

        try:
            estimate = analyze_meal_image(image_path, image_content_type)
        except NutritionError as error:
            meal.status = "analysis_failed"
            meal.analysis_error = str(error)
            db.commit()

            print(f"[{meal.twilio_message_sid}] Nutrition error: {error}")
            message = (
                "Your meal photo was saved, but I couldn’t estimate "
                "its nutrition. Please try another photo."
            )
        else:
            nutrition_record = NutritionEstimateRecord(
                meal_id=meal.id,
                food_items=[
                    item.model_dump(mode="json")
                    for item in estimate.food_items
                ],
                calories_min=estimate.total_calories_kcal.minimum,
                calories_max=estimate.total_calories_kcal.maximum,
                protein_min=estimate.total_protein_g.minimum,
                protein_max=estimate.total_protein_g.maximum,
                carbs_min=estimate.total_carbs_g.minimum,
                carbs_max=estimate.total_carbs_g.maximum,
                fat_min=estimate.total_fat_g.minimum,
                fat_max=estimate.total_fat_g.maximum,
                fibre_min=estimate.total_fibre_g.minimum,
                fibre_max=estimate.total_fibre_g.maximum,
                model_name="gemini-flash-latest",
            )

            db.add(nutrition_record)
            meal.status = "analyzed"
            meal.analysis_error = None

            # Make the new estimate visible to the aggregation query while
            # keeping the estimate and status in the same transaction.
            db.flush()
            user = db.get(User, meal.user_phone)
            totals = get_daily_nutrition_totals(db, meal.user_phone)

            db.commit()

            print(f"[{meal.twilio_message_sid}] Gemini analysis complete.")
            message = format_nutrition_reply(estimate, meal.meal_type)
            if (
                user is not None
                and user.calorie_goal is not None
                and user.protein_goal is not None
            ):
                message += "\n\n" + format_daily_progress(
                    totals,
                    user.calorie_goal,
                    user.protein_goal,
                )

        try:
            send_whatsapp_message(recipient, sender, message)
            print(f"[{meal.twilio_message_sid}] WhatsApp reply sent.")
        except Exception as error:
            print(
                f"[{meal.twilio_message_sid}] "
                f"Could not send WhatsApp reply: {error}"
            )
    finally:
        db.close()


# for testing only
@app.get("/health")
def read_root():
    return {"status": "ok"}

@app.post("/test/users/{phone_number}", tags=["debug"])
def test_user(phone_number: str, db: Session = Depends(get_db)):
    """TEMPORARY: dev-only endpoint for verifying user persistence. Remove before deploy."""
    user = get_or_create(db, normalize_phone(phone_number))
    return {
        "phone": user.phone,
        "onboarding_state": user.onboarding_state,
        "calorie_goal": user.calorie_goal,
        "protein_goal": user.protein_goal,
        "created_at": user.created_at,
    }


@app.post("/whatsapp")
async def whatsapp(
    background_tasks: BackgroundTasks,
    from_number: str = Form("", alias="From"),
    to_number: str = Form("", alias="To"),
    body: str = Form("", alias="Body"),
    message_sid: str = Form(..., alias="MessageSid"),
    num_media: int = Form(0, alias="NumMedia"),
    media_url: str | None = Form(None, alias="MediaUrl0"),
    media_content_type: str | None = Form(None, alias="MediaContentType0"),
    db: Session = Depends(get_db),
):
    phone = normalize_phone(from_number)
    user = get_or_create(db, phone)

    if user.onboarding_state != ACTIVE:
      reply = handle_message(user, body)

    elif num_media > 0:
        if media_url is None or not (media_content_type or "").startswith("image/"):
            reply = "Please send an image of your meal."
        elif get_meal_by_message_sid(db, message_sid) is not None:
            reply = "We've already received this meal. Please send a new one."
        else:
            try:
                print(f"[{message_sid}] Downloading image from Twilio...")
                stored_image_path = download_and_save_image(
                    media_url=media_url,
                    message_sid=message_sid,
                    content_type=media_content_type,
                )
                print(f"[{message_sid}] Image saved at: {stored_image_path}")
            except StorageError as error:
                print(f"[{message_sid}] Storage error: {error}")
                reply = "I couldn’t save that image. Please try sending it again."
            else:
                meal_type = infer_meal_type(
                    get_current_time_in_timezone().time()
                )
                meal = Meal(
                    user_phone=phone,
                    twilio_message_sid=message_sid,
                    image_url=media_url,
                    image_content_type=media_content_type,
                    meal_type=meal_type,
                    status="stored",
                )
                db.add(meal)
                db.flush()

                background_tasks.add_task(
                    analyze_and_reply,
                    meal.id,
                    stored_image_path,
                    media_content_type,
                    from_number,
                    to_number,
                )
                reply = (
                    f"Got it — I’m analyzing your {meal_type}. "
                    "I’ll send the nutrition estimate shortly."
                )
    else:
        reply = "Please send an image of your meal."

    db.commit()

    twiml = MessagingResponse()
    twiml.message(reply)

    return Response(content=str(twiml), media_type="application/xml")
