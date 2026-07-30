from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import messaging
from database import Base
from models import OutboundMessage


def make_session(tmp_path):
    database_path = tmp_path / "test_messaging.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_send_with_audit_retries_then_succeeds(monkeypatch, tmp_path):
    db = make_session(tmp_path)
    attempts = []

    def flaky_send(recipient, sender, message):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("transient send failure")
        return "SM_SENT_SID"

    monkeypatch.setattr(messaging.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(messaging, "send_whatsapp_message", flaky_send)

    result = messaging.send_with_audit(
        db,
        recipient="whatsapp:+919999999999",
        sender="whatsapp:+14155238886",
        body="hello",
        phone="919999999999",
        kind="meal_analysis",
    )

    assert len(attempts) == 3
    assert result.status == "sent"
    assert result.twilio_sid == "SM_SENT_SID"
    assert result.attempt_count == 3

    saved = db.scalar(select(OutboundMessage).where(OutboundMessage.id == result.id))
    assert saved is not None
    assert saved.status == "sent"
    assert saved.kind == "meal_analysis"
    db.close()


def test_send_with_audit_records_permanent_failure_without_raising(
    monkeypatch, tmp_path
):
    db = make_session(tmp_path)

    def always_fail(recipient, sender, message):
        raise RuntimeError("permanent send failure")

    monkeypatch.setattr(messaging.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(messaging, "send_whatsapp_message", always_fail)

    result = messaging.send_with_audit(
        db,
        recipient="whatsapp:+919999999999",
        sender="whatsapp:+14155238886",
        body="hello",
        phone="919999999999",
        kind="reminder",
    )

    assert result.status == "failed"
    assert result.twilio_sid is None
    assert result.attempt_count == 3
    assert "permanent send failure" in result.error

    saved = db.scalar(select(OutboundMessage).where(OutboundMessage.id == result.id))
    assert saved.status == "failed"
    db.close()
