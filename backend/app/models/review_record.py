"""ReviewRecord ORM model.

A ``ReviewRecord`` is one self-rated review of a ``WordEntry``: the Difficulty
rating the Learner selected and the interval the scheduler assigned for that
review. The history drives subsequent SM-2 interval computation (Req 7).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.word_entry import WordEntry


def _uuid() -> str:
    """Return a new random UUID as a string (used for primary keys)."""
    return str(uuid.uuid4())


class ReviewRecord(Base):
    """A single review event for a word.

    Attributes:
        id: UUID primary key.
        word_id: FK to the reviewed ``WordEntry``.
        rating: Difficulty rating 1..4 (hardest=1 .. easiest=4).
        interval_days: Interval assigned by this review.
        reviewed_at: When the review was recorded.
        word: The reviewed word.
    """

    __tablename__ = "review_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    word_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("word_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    word: Mapped["WordEntry"] = relationship(back_populates="review_records")
