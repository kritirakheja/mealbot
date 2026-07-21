from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from database import Base, engine, get_db
from models import User

from conversation import handle_message, normalize_phone


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(lifespan=lifespan)

# helper function


def get_or_create(db: Session, phone: str) -> User:
    user = db.get(User, phone)
    if user is None:
        user = User(phone=phone)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

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
    from_number: str = Form(..., alias="From"),
    body: str = Form(..., alias="Body"),
    message_sid: str = Form(..., alias="MessageSid"),
    db: Session = Depends(get_db),
):
    phone = normalize_phone(from_number)
    user = get_or_create(db, phone)

    reply = handle_message(user, body)
    db.commit()

    twiml = MessagingResponse()
    twiml.message(reply)

    return Response(content=str(twiml), media_type="application/xml")
