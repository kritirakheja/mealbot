from fastapi import FastAPI
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse

app = FastAPI()

@app.get("/health")
def read_root():
    return {"status": "ok"}


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
