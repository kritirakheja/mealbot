from sqlalchemy import select


from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from database import Base, engine, get_db
from models import Meal, User

from conversation import ACTIVE, handle_message, normalize_phone
from meal_logic import get_current_time_in_timezone, infer_meal_type

from storage import StorageError, download_and_save_image

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

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
    from_number: str = Form("", alias="From"),
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
                download_and_save_image(
                    media_url=media_url,
                    message_sid=message_sid,
                    content_type=media_content_type,
                )
            except StorageError:
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
                reply = (
                    f"Saved your {meal_type} photo. "
                    "I’ll estimate its nutrition next."
                )
    else:
        reply = "Please send an image of your meal."

    db.commit()

    twiml = MessagingResponse()
    twiml.message(reply)

    return Response(content=str(twiml), media_type="application/xml")
