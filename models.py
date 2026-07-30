from typing import Optional
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)

from database import Base


class User(Base):
    __tablename__ = "users"

    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    calorie_goal: Mapped[Optional[int]] = mapped_column(default=None)
    protein_goal: Mapped[Optional[int]] = mapped_column(default=None)
    onboarding_state: Mapped[str] = mapped_column(String(32), default="new")
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_phone: Mapped[str] = mapped_column(
        ForeignKey("users.phone"), nullable=False, index=True
    )
    twilio_message_sid: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    image_url: Mapped[str] = mapped_column(String, nullable=False)
    image_content_type: Mapped[Optional[str]] = mapped_column(String, default=None)

    meal_type: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    status: Mapped[str] = mapped_column(
        String(32), default="awaiting_meal_type"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class NutritionEstimate(Base):
    __tablename__ = "nutrition_estimates"

    id: Mapped[int] = mapped_column(primary_key=True)
    meal_id: Mapped[int] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )

    food_items: Mapped[list[dict]] = mapped_column(JSON)

    calories_min: Mapped[float] = mapped_column(Float)
    calories_max: Mapped[float] = mapped_column(Float)
    protein_min: Mapped[float] = mapped_column(Float)
    protein_max: Mapped[float] = mapped_column(Float)
    carbs_min: Mapped[float] = mapped_column(Float)
    carbs_max: Mapped[float] = mapped_column(Float)
    fat_min: Mapped[float] = mapped_column(Float)
    fat_max: Mapped[float] = mapped_column(Float)
    fibre_min: Mapped[float] = mapped_column(Float)
    fibre_max: Mapped[float] = mapped_column(Float)

    model_name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

class MealAnalysisAttempt(Base):
    __tablename__ = "meal_analysis_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_id: Mapped[int] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision_action: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class InboundMessage(Base):
    """One row per inbound Twilio webhook delivery, keyed by MessageSid.

    Used both as the audit trail for inbound messages/replies and as the
    idempotency guard for webhook retries (Twilio redelivers on timeout).
    """

    __tablename__ = "inbound_messages"

    message_sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    body: Mapped[str] = mapped_column(Text)
    num_media: Mapped[int] = mapped_column(Integer, default=0)
    reply_body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class OutboundMessage(Base):
    """Audit trail for proactive (Twilio REST API) sends, e.g. meal analysis
    replies and reminders. Onboarding/ack replies go out via TwiML and are
    covered by InboundMessage.reply_body instead."""

    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    meal_id: Mapped[int | None] = mapped_column(
        ForeignKey("meals.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16))
    twilio_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
