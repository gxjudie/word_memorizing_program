"""WordEntry ORM model.

A ``WordEntry`` is one memorized Japanese vocabulary item owned by a Learner.
It carries both the lexical fields (word form, reading, meaning) and the
spaced-repetition scheduling state (ease, interval, repetitions, next review
date). A ``(user_id, word_form)`` unique constraint enforces per-collection
dedupe (Req 1.4).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.generated_sentence import GeneratedSentence
    from app.models.review_record import ReviewRecord
    from app.models.user import User


def _uuid() -> str:
    """Return a new random UUID as a string (used for primary keys)."""
    return str(uuid.uuid4())


class WordEntry(Base):
    """A vocabulary entry with its scheduling state.

    Attributes:
        id: UUID primary key.
        user_id: FK to the owning ``User``.
        word_form: Japanese word, 1-50 chars (Req 1.1); unique per user (Req 1.4).
        reading: Hiragana/kana reading, 1-100 chars.
        meaning: Definition/gloss, 1-500 chars.
        next_review_date: Scheduling state; set to creation date so new words
            are due immediately (Req 1.5), updated by the scheduler (Req 7.4).
        ease_factor: SM-2 ease, default 2.5.
        interval_days: Last computed interval in days, default 0.
        repetitions: Consecutive successful reviews, default 0.
        created_at: Row creation timestamp.
        user: The owning Learner.
        review_records: Review history for this word (1-to-many).
        generated_sentences: Previously generated sentences for this word.
    """

    __tablename__ = "word_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "word_form", name="uq_word_entry_user_word"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    word_form: Mapped[str] = mapped_column(String(50), nullable=False)
    reading: Mapped[str] = mapped_column(String(100), nullable=False)
    meaning: Mapped[str] = mapped_column(String(500), nullable=False)
    next_review_date: Mapped[date] = mapped_column(Date, nullable=False)
    ease_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="words")
    review_records: Mapped[list["ReviewRecord"]] = relationship(
        back_populates="word",
        cascade="all, delete-orphan",
    )
    generated_sentences: Mapped[list["GeneratedSentence"]] = relationship(
        back_populates="word",
        cascade="all, delete-orphan",
    )
