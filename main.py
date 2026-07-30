import asyncio
import os

from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from analysis_job import analyze_and_reply, recover_stuck_meals
from commands import HELP_TEXT, build_today_summary, get_pending_meal, undo_last_meal
from database import Base, engine, get_db
from models import InboundMessage, Meal, User

from conversation import ACTIVE, handle_message, normalize_phone
from meal_logic import get_current_time_in_timezone, infer_meal_type
from reminders import start_reminder_tasks

from storage import StorageError, download_and_save_image

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
VALIDATE_TWILIO_SIGNATURE = os.getenv(
    "TWILIO_VALIDATE_SIGNATURE", "true"
).lower() != "false"


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


@app.get("/health")
def read_root():
    return {"status": "ok"}


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
