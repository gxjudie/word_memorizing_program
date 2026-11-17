"""RevokedAppSession ORM model.

Logout records the App_Session ``sid`` here until the session's ``exp`` so a
revoked session token is rejected like a missing one (Req 9.13). Rows can be
purged once ``expires_at`` has passed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RevokedAppSession(Base):
    """A logged-out App_Session identifier.

    Attributes:
        sid: The App_Session ``sid`` claim (primary key).
        expires_at: The session's original expiry; indexed for purging.
    """

    __tablename__ = "revoked_app_sessions"

    sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True
    )
