from typing import Optional
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

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
