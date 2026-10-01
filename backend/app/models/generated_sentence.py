"""GeneratedSentence ORM model.

A ``GeneratedSentence`` is an LLM-produced Example_Sentence for a ``WordEntry``,
persisted with its structured tokenization. The most recent row for a word is
the "immediately preceding" sentence used for the difference check (Req 4.3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.word_entry import WordEntry


def _uuid() -> str:
    """Return a new random UUID as a string (used for primary keys)."""
    return str(uuid.uuid4())


class GeneratedSentence(Base):
    """A generated example sentence and its structured tokens.

    Attributes:
        id: UUID primary key.
        word_id: FK to the target ``WordEntry``.
        text: The full sentence text.
        tokens: Structured tokenization (list of token dicts) that drives the
            renderer and lookup panel. Stored as JSON.
        created_at: Row creation timestamp; the latest row is the "immediately
            preceding review" sentence (Req 4.3).
        word: The target word.
    """

    __tablename__ = "generated_sentences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    word_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("word_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    word: Mapped["WordEntry"] = relationship(back_populates="generated_sentences")
