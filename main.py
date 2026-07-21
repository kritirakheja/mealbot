from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Response
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse

from database import Base, engine, get_db
from models import User

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def read_root():
    return {"status": "ok"}

@app.post("/test/users/{phone_number}", tags=["debug"])
def get_or_create_user(phone_number: str, db: Session = Depends(get_db)):
    """TEMPORARY: dev-only endpoint for verifying user persistence. Remove before deploy."""


@app.post("/whatsapp")
async def whatsapp(
    from_number: str = Form(..., alias="From"),
    body: str = Form(..., alias="Body"),
    message_sid: str = Form(..., alias="MessageSid"),
):
    # Inspect the incoming request
    print(f"From: {from_number}")
    print(f"Body: {body}")
    print(f"MessageSid: {message_sid}")

    # Create a Twilio response
    twiml = MessagingResponse()
    twiml.message("Hi! Send me a meal photo to log it.")

    # Return the TwiML XML
    return Response(
        content=str(twiml),
        media_type="application/xml",
    )
