"""Outbound WhatsApp messaging through Twilio."""

import os
import time

from sqlalchemy.orm import Session
from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from models import OutboundMessage

RETRY_BACKOFF_SECONDS = (1, 3)


def send_whatsapp_message(
    recipient: str,
    sender: str,
    message: str,
) -> str:
    """Send one WhatsApp message via Twilio's REST API. Returns the message SID."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        raise RuntimeError("Twilio credentials are not configured.")

    client = Client(account_sid, auth_token)
    result = client.messages.create(
        to=recipient,
        from_=sender,
        body=message,
    )
    return result.sid


def send_with_audit(
    db: Session,
    *,
    recipient: str,
    sender: str,
    body: str,
    phone: str,
    kind: str,
    meal_id: int | None = None,
) -> OutboundMessage:
    """Send a proactive WhatsApp message, retrying transient failures, and
    record the outcome as an OutboundMessage row (never raises)."""
    last_error: str | None = None
    twilio_sid: str | None = None
    attempt_count = 0

    for attempt, delay in enumerate((0, *RETRY_BACKOFF_SECONDS), start=1):
        if delay:
            time.sleep(delay)
        attempt_count = attempt
        try:
            twilio_sid = send_whatsapp_message(recipient, sender, body)
            last_error = None
            break
        except (TwilioException, RuntimeError, OSError) as error:
            last_error = str(error)

    outbound_message = OutboundMessage(
        phone=phone,
        body=body,
        kind=kind,
        meal_id=meal_id,
        status="sent" if last_error is None else "failed",
        twilio_sid=twilio_sid,
        error=last_error,
        attempt_count=attempt_count,
    )
    db.add(outbound_message)
    db.commit()
    return outbound_message
