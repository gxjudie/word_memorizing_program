"""StudySettingsService: read and update one Learner's Study_Settings (Req 11).

Study_Settings are two columns on ``users`` (``new_word_limit``,
``review_word_limit``), so every account has exactly one pair (Req 11.1) and
rows that were never saved carry the server defaults 50 / 100 (Req 11.2).

Every method is keyed by the App_Session's user id, so a Learner can read or
modify only their own settings (Req 11.8). ``update`` accepts only an
already-validated :class:`~app.schemas.study_settings.StudySettingsIn`, so
invalid values never reach the database (Req 11.5).
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.study_settings import StudySettingsIn, StudySettingsOut

__all__ = ["StudySettingsService", "UserNotFoundError"]


class UserNotFoundError(LookupError):
    """Raised when no ``users`` row exists for the given id.

    ``CurrentUser`` guarantees the row exists on the HTTP path, so this only
    surfaces on direct service calls with an unknown id.
    """


class StudySettingsService:
    """Concrete service over the two Study_Settings columns on ``users``."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def _load_user(self, user_id: str) -> User:
        user = self._db.get(User, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        return user

    def get(self, user_id: str) -> StudySettingsOut:
        """Return the Learner's stored limits (Req 11.2, 11.3)."""
        return StudySettingsOut.model_validate(self._load_user(user_id))

    def update(self, user_id: str, data: StudySettingsIn) -> StudySettingsOut:
        """Store both limits and commit once (Req 11.4).

        Either both values are stored or neither is. On a database error the
        session is rolled back and the error is re-raised.
        """
        user = self._load_user(user_id)
        try:
            user.new_word_limit = data.new_word_limit
            user.review_word_limit = data.review_word_limit
            self._db.commit()
        except SQLAlchemyError:
            self._db.rollback()
            raise
        return StudySettingsOut.model_validate(user)
