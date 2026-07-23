from typing import Optional
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import (
    JSON,
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
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
