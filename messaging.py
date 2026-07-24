"""Outbound WhatsApp messaging through Twilio."""

import os
from twilio.rest import Client


def send_whatsapp_message(
    recipient: str,
    sender: str,
    message: str,
) -> None:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        raise RuntimeError("Twilio credentials are not configured.")

    client = Client(account_sid, auth_token)
    client.messages.create(
        to=recipient,
        from_=sender,
        body=message,
    )
