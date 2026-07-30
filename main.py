import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, Response
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from database import Base, SessionLocal, engine, get_db
from models import (
    InboundMessage,
    Meal,
    NutritionEstimate as NutritionEstimateRecord,
    User,
)

from conversation import ACTIVE, handle_message, normalize_phone
from meal_logic import get_current_time_in_timezone, infer_meal_type
from messaging import send_with_audit
from reminders import start_reminder_tasks, whatsapp_address

from storage import StorageError, download_and_save_image

from nutrition import (
    NutritionError,
    analyze_meal_image,
    format_nutrition_reply,
)

from decision_policy import MealAction, decide_meal_action
from models import MealAnalysisAttempt

PENDING_MEAL_STATUSES = {
    "needs_new_image",
    "awaiting_dish_choice",
    "awaiting_serving_size",
}
ANALYSIS_RETRY_BACKOFF_SECONDS = (1, 3)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
VALIDATE_TWILIO_SIGNATURE = os.getenv(
    "TWILIO_VALIDATE_SIGNATURE", "true"
).lower() != "false"


def recover_stuck_meals() -> int:
    """Recover meals left at status='stored' by an interrupted background
    task (e.g. a deploy or crash mid-analysis). A freshly started process
    can't have any in-flight BackgroundTask, so anything found here belongs
    to a previous run."""
    sender = os.getenv("TWILIO_WHATSAPP_FROM")
    if sender and not sender.startswith("whatsapp:"):
        sender = f"whatsapp:{sender}"

    db = SessionLocal()
    recovered = 0
    try:
        stuck_meals = db.scalars(select(Meal).where(Meal.status == "stored")).all()
        for meal in stuck_meals:
            meal.analysis_error = "Processing was interrupted by a server restart."
            meal.status = "analysis_failed"
            db.add(
                MealAnalysisAttempt(
                    meal_id=meal.id,
                    provider_error=meal.analysis_error,
                )
            )
            db.flush()
            if sender:
                send_with_audit(
                    db,
                    recipient=whatsapp_address(meal.user_phone),
                    sender=sender,
                    body=(
                        "Sorry — something interrupted processing your last "
                        "meal photo. Please resend it and I’ll try again."
                    ),
                    phone=meal.user_phone,
                    kind="stuck_meal_recovery",
                    meal_id=meal.id,
                )
            recovered += 1
        db.commit()
    finally:
        db.close()

    if recovered:
        print(f"[startup] Recovered {recovered} meal(s) stuck in 'stored'.")
    return recovered


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    await asyncio.to_thread(recover_stuck_meals)
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


def get_pending_meal(db: Session, phone: str) -> Meal | None:
    """Return the most recent meal still waiting on a clarification, if any."""
    statement = (
        select(Meal)
        .where(Meal.user_phone == phone, Meal.status.in_(PENDING_MEAL_STATUSES))
        .order_by(Meal.created_at.desc())
    )
    return db.scalars(statement).first()


def validate_twilio_signature(request: Request, form: dict) -> bool:
    if not VALIDATE_TWILIO_SIGNATURE:
        return True

    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        return False

    validator = RequestValidator(auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = f"{PUBLIC_BASE_URL}/whatsapp" if PUBLIC_BASE_URL else str(request.url)
    return validator.validate(url, form, signature)


def local_day_bounds_utc(local_now: datetime) -> tuple[datetime, datetime]:
    """Return the [start, end) of local_now's calendar day, in naive UTC
    (to match how SQLite stores timestamps)."""
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def get_daily_nutrition_totals(
    db: Session,
    user_phone: str,
    local_now: datetime | None = None,
) -> DailyNutritionTotals:
    """Sum analyzed meals within the user's current local calendar day."""
    local_now = local_now or get_current_time_in_timezone()
    start_utc, end_utc = local_day_bounds_utc(local_now)

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


def format_remaining(consumed_min: float, consumed_max: float, goal: float, unit: str) -> str:
    """Describe how much of `goal` is left, given a [consumed_min, consumed_max] range."""
    remaining_low = goal - consumed_max
    remaining_high = goal - consumed_min
    if remaining_high <= 0:
        return "goal reached"
    remaining_low = max(remaining_low, 0)
    if remaining_low == remaining_high:
        return f"{remaining_high:,.0f}{unit} left"
    return f"{remaining_low:,.0f}–{remaining_high:,.0f}{unit} left"


def format_daily_progress(
    totals: DailyNutritionTotals,
    calorie_goal: int,
    protein_goal: int,
) -> str:
    return (
        "Today's progress:\n"
        f"• Calories: {totals.calories_min:.0f}–"
        f"{totals.calories_max:.0f} / {calorie_goal:,} kcal "
        f"({format_remaining(totals.calories_min, totals.calories_max, calorie_goal, ' kcal')})\n"
        f"• Protein: {totals.protein_min:.0f}–"
        f"{totals.protein_max:.0f} / {protein_goal}g "
        f"({format_remaining(totals.protein_min, totals.protein_max, protein_goal, 'g')})"
    )


HELP_TEXT = (
    "Here's what I can do:\n"
    "• Send a photo of your meal to log it\n"
    "• today — see today's progress\n"
    "• undo — remove your last logged meal\n"
    "• goals — update your calorie/protein goals\n"
    "• reminders off / reminders on — mute or unmute meal reminders\n"
    "• reset — start over completely"
)


def build_today_summary(db: Session, phone: str) -> str:
    local_now = get_current_time_in_timezone()
    start_utc, end_utc = local_day_bounds_utc(local_now)

    statement = (
        select(Meal, NutritionEstimateRecord)
        .join(NutritionEstimateRecord, NutritionEstimateRecord.meal_id == Meal.id)
        .where(
            Meal.user_phone == phone,
            Meal.status == "analyzed",
            Meal.created_at >= start_utc,
            Meal.created_at < end_utc,
        )
        .order_by(Meal.created_at)
    )
    rows = db.execute(statement).all()

    if not rows:
        lines = ["No meals logged yet today. Send a photo whenever you eat!"]
    else:
        lines = ["Today so far:"]
        for meal, estimate in rows:
            foods = ", ".join(item["name"] for item in estimate.food_items) or "meal"
            meal_label = meal.meal_type.title() if meal.meal_type else "Meal"
            lines.append(
                f"• {meal_label} — {foods}: "
                f"{estimate.calories_min:.0f}–{estimate.calories_max:.0f} kcal"
            )

    user = db.get(User, phone)
    if user is not None and user.calorie_goal is not None and user.protein_goal is not None:
        totals = get_daily_nutrition_totals(db, phone, local_now)
        lines.append("")
        lines.append(format_daily_progress(totals, user.calorie_goal, user.protein_goal))

    return "\n".join(lines)


def undo_last_meal(db: Session, phone: str) -> str:
    local_now = get_current_time_in_timezone()
    start_utc, end_utc = local_day_bounds_utc(local_now)

    statement = (
        select(Meal)
        .where(
            Meal.user_phone == phone,
            Meal.status == "analyzed",
            Meal.created_at >= start_utc,
            Meal.created_at < end_utc,
        )
        .order_by(Meal.created_at.desc())
    )
    meal = db.scalars(statement).first()
    if meal is None:
        return "You don't have a meal logged today to undo."

    estimate = db.scalar(
        select(NutritionEstimateRecord).where(NutritionEstimateRecord.meal_id == meal.id)
    )
    food_names = (
        ", ".join(item["name"] for item in estimate.food_items)
        if estimate is not None and estimate.food_items
        else "that meal"
    )
    if estimate is not None:
        db.delete(estimate)
    meal.status = "undone"
    db.flush()

    message = f"Removed: {food_names}."
    user = db.get(User, phone)
    if user is not None and user.calorie_goal is not None and user.protein_goal is not None:
        totals = get_daily_nutrition_totals(db, phone, local_now)
        message += "\n\n" + format_daily_progress(totals, user.calorie_goal, user.protein_goal)
    return message


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

        analysis = None
        analysis_error: NutritionError | None = None
        for attempt, delay in enumerate(
            (0, *ANALYSIS_RETRY_BACKOFF_SECONDS), start=1
        ):
            if delay:
                time.sleep(delay)
            try:
                analysis = analyze_meal_image(image_path, image_content_type)
                analysis_error = None
                break
            except NutritionError as error:
                analysis_error = error
                print(
                    f"[{meal.twilio_message_sid}] Gemini attempt "
                    f"{attempt} failed: {error}"
                )

        if analysis_error is not None:
            meal.status = "analysis_failed"
            meal.analysis_error = str(analysis_error)
            db.add(
                MealAnalysisAttempt(
                    meal_id=meal.id,
                    provider_error=str(analysis_error),
                )
            )
            db.commit()

            print(f"[{meal.twilio_message_sid}] Nutrition error: {analysis_error}")
            message = (
                "Your meal photo was saved, but I couldn’t estimate "
                "its nutrition. Please try another photo."
            )
        else:
            decision = decide_meal_action(analysis)
            db.add(
                MealAnalysisAttempt(
                    meal_id=meal.id,
                    result=analysis.model_dump(mode="json"),
                    decision_action=decision.action.value,
                    decision_reason=decision.reason,
                )
            )

            meal.analysis_error = None

            if decision.action == MealAction.AUTO_LOG:
                estimate = analysis.estimate
                assert estimate is not None

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
            else:
                pending_statuses = {
                    MealAction.REQUEST_NEW_IMAGE: "needs_new_image",
                    MealAction.ASK_DISH_CHOICE: "awaiting_dish_choice",
                    MealAction.ASK_SERVING_SIZE: "awaiting_serving_size",
                }
                meal.status = pending_statuses[decision.action]
                message = decision.user_message
                db.commit()

        outbound_message = send_with_audit(
            db,
            recipient=recipient,
            sender=sender,
            body=message,
            phone=meal.user_phone,
            kind="meal_analysis",
            meal_id=meal.id,
        )
        if outbound_message.status == "sent":
            print(f"[{meal.twilio_message_sid}] WhatsApp reply sent.")
        else:
            print(
                f"[{meal.twilio_message_sid}] "
                f"Could not send WhatsApp reply: {outbound_message.error}"
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
    request: Request,
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
    form_data = dict(await request.form())
    if not validate_twilio_signature(request, form_data):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    existing_inbound = db.get(InboundMessage, message_sid)
    if existing_inbound is not None:
        twiml = MessagingResponse()
        twiml.message(existing_inbound.reply_body)
        return Response(content=str(twiml), media_type="application/xml")

    phone = normalize_phone(from_number)
    user = get_or_create(db, phone)
    text = body.strip().lower()

    if user.onboarding_state != ACTIVE or text in {"start", "reset", "goals"}:
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
    elif text in {"help", "menu"}:
        reply = HELP_TEXT

    elif text in {"today", "progress"}:
        reply = build_today_summary(db, phone)

    elif text in {"undo", "delete last meal", "delete"}:
        reply = undo_last_meal(db, phone)

    elif text in {"reminders off", "mute reminders", "pause reminders"}:
        user.reminders_enabled = False
        reply = "Reminders paused. Send 'reminders on' to turn them back on."

    elif text in {"reminders on", "unmute reminders", "resume reminders"}:
        user.reminders_enabled = True
        reply = "Reminders are back on."

    else:
        pending_meal = get_pending_meal(db, phone)
        if pending_meal is not None:
            pending_meal.clarification_text = body
            pending_meal.status = "clarification_closed"
            reply = (
                "Thanks — I’ve noted that. I still don’t have a reliable "
                "nutrition estimate for that meal, so please send a fresh "
                "photo and I’ll try again."
            )
        else:
            reply = "Please send an image of your meal. Or type 'help' to see what else I can do."

    db.add(
        InboundMessage(
            message_sid=message_sid,
            phone=phone,
            body=body,
            num_media=num_media,
            reply_body=reply,
        )
    )
    db.commit()

    twiml = MessagingResponse()
    twiml.message(reply)

    return Response(content=str(twiml), media_type="application/xml")
