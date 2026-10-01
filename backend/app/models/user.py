"""User ORM model.

A ``User`` is the authenticated Learner. The backend owns identity (see the
"opencode-style" auth decision in the design); the user authenticates to this
application, not to the LLM provider.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.word_entry import WordEntry


def _uuid() -> str:
    """Return a new random UUID as a string (used for primary keys)."""
    return str(uuid.uuid4())


class User(Base):
    """A Learner account.

    Attributes:
        id: UUID primary key.
        email: Unique login identity used for auth and LLM-proxy authorization.
        password_hash: argon2 hash; ``None`` for OAuth-only accounts.
        created_at: Row creation timestamp.
        words: The Learner's Word_Collection (1-to-many).
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    words: Mapped[list["WordEntry"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
