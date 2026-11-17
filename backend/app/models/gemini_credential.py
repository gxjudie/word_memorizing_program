"""GeminiCredential ORM model.

Stores one Learner's own Gemini_API_Key (BYOK) as a Fernet ciphertext. The
plaintext key is never persisted (Req 10.11); only the last-4 ``key_hint``
ever leaves the backend (Req 10.12).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.user import User


def _uuid() -> str:
    """Return a new random UUID as a string (used for primary keys)."""
    return str(uuid.uuid4())


class GeminiCredential(Base):
    """The encrypted Gemini API key belonging to one Learner.

    Attributes:
        id: UUID primary key.
        user_id: Unique FK to the owning ``User`` (ON DELETE CASCADE), so a
            Learner has at most one stored key.
        api_key_encrypted: Fernet ciphertext of the trimmed key.
        key_hint: Last 4 characters of the key, shown in the UI.
        created_at: Row creation timestamp.
        updated_at: Bumped whenever the key is replaced (Req 10.14).
        user: The owning Learner.
    """

    __tablename__ = "gemini_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="gemini_credential")

    def __repr__(self) -> str:
        """Return a representation that never includes the ciphertext."""
        return f"GeminiCredential(id={self.id!r}, user_id={self.user_id!r})"
