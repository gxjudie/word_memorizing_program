"""User ORM model.

A ``User`` is the authenticated Learner associated with a verified Google
OpenID Connect subject. The application stores no local password credential
and no Google token (Req 9.12).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.gemini_credential import GeminiCredential
    from app.models.word_entry import WordEntry


def _uuid() -> str:
    """Return a new random UUID as a string (used for primary keys)."""
    return str(uuid.uuid4())


class User(Base):
    """A Learner account identified by a verified Google OIDC subject.

    Attributes:
        id: UUID primary key.
        email: Email claim from the validated Google ID token (refreshed on
            each sign-in, never used as the account key).
        name: Optional display name from the validated ID token.
        oidc_issuer: ID-token issuer, stored normalized as
            ``https://accounts.google.com``.
        oidc_subject: Google ``sub`` claim.
        created_at: Row creation timestamp.
        gemini_credential: The Learner's encrypted Gemini API key (0..1).
        words: The Learner's Word_Collection (1-to-many).
    """

    __tablename__ = "users"
    __table_args__ = (
        # One app account per verified Google identity (Req 9.9).
        UniqueConstraint(
            "oidc_issuer",
            "oidc_subject",
            name="uq_user_oidc_subject",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oidc_issuer: Mapped[str] = mapped_column(String(2048), nullable=False)
    oidc_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    gemini_credential: Mapped["GeminiCredential | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
    )
    words: Mapped[list["WordEntry"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
