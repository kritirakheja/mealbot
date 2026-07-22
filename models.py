from typing import Optional
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from database import Base


class User(Base):
    __tablename__ = "users"

    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    calorie_goal: Mapped[Optional[int]] = mapped_column(default=None)
    protein_goal: Mapped[Optional[int]] = mapped_column(default=None)
    onboarding_state: Mapped[str] = mapped_column(String(32), default="new")
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
