"""Word Service — add and list Word_Entries (Req 1).

This service owns the business rules for building a Learner's Word_Collection:

* **Validation (Req 1.1-1.3).** Incoming data is validated through the
  ``WordInput`` Pydantic schema, whose per-field validators enforce the length
  bounds and reject missing / whitespace-only fields. A submission with several
  offending fields surfaces *each* offending field by name (Property 2) — the
  service re-raises the Pydantic ``ValidationError`` unchanged so that
  field-exact information reaches the caller.
* **Dedupe (Req 1.4).** Within a single Learner's collection the Japanese word
  form is unique. An exact match raises ``DuplicateError`` and leaves the
  existing entry untouched — nothing is written.
* **Due-today (Req 1.5).** A freshly created word's ``next_review_date`` is set
  to the current date, so it is immediately a Due_Word.

The service takes a SQLAlchemy ``Session`` and contains no HTTP concerns, so it
is testable without a web server (see the layer separation in the design).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.word_entry import WordEntry
from app.schemas.word import WordInput

__all__ = ["WordService", "DuplicateError"]


class DuplicateError(Exception):
    """Raised when a word form already exists in the Learner's collection.

    Signals the duplicate-entry rejection required by Req 1.4. When raised, no
    ``WordEntry`` is created and the existing entry is left unmodified.

    Attributes:
        word_form: The duplicate Japanese word form that was rejected.
    """

    def __init__(self, word_form: str) -> None:
        self.word_form = word_form
        super().__init__(f"word form already exists: {word_form!r}")


class WordService:
    """Add words to and list a Learner's Word_Collection.

    The service is a thin, stateless wrapper over a SQLAlchemy ``Session``; a new
    instance may be constructed per request/unit of work.
    """

    def __init__(self, db: Session) -> None:
        """Store the SQLAlchemy session used for persistence and queries."""
        self._db = db

    def add_word(
        self,
        user_id: str,
        data: WordInput,
        today: date | None = None,
    ) -> WordEntry:
        """Validate, dedupe, and persist a new Word_Entry (Req 1.1-1.5).

        Args:
            user_id: Owner of the new entry.
            data: The already-parsed add-word input. Passing a ``WordInput``
                means the field-level validation (Req 1.1-1.3) has run; callers
                that start from raw data should construct ``WordInput`` so a
                ``pydantic.ValidationError`` naming each offending field is
                raised before this method is reached (Property 2).
            today: The current date, injected for testability. Defaults to
                ``date.today()`` when omitted.

        Returns:
            The persisted ``WordEntry``, with ``next_review_date`` set to
            ``today`` so it is immediately a Due_Word (Req 1.5).

        Raises:
            DuplicateError: If an entry with the same ``word_form`` already
                exists for this user (Req 1.4). The existing entry is left
                unmodified and nothing is written.
        """
        creation_date = today if today is not None else date.today()

        # Exact-match dedupe within this Learner's collection (Req 1.4). The
        # (user_id, word_form) unique constraint is the backstop; this check
        # produces the domain-level DuplicateError without relying on a flush.
        existing = self._db.execute(
            select(WordEntry).where(
                WordEntry.user_id == user_id,
                WordEntry.word_form == data.word_form,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateError(data.word_form)

        entry = WordEntry(
            user_id=user_id,
            word_form=data.word_form,
            reading=data.reading,
            meaning=data.meaning,
            next_review_date=creation_date,  # due immediately (Req 1.5)
        )
        self._db.add(entry)
        self._db.commit()
        self._db.refresh(entry)
        return entry

    def list_collection(self, user_id: str) -> list[WordEntry]:
        """Return all Word_Entries belonging to a Learner.

        Args:
            user_id: The owning Learner.

        Returns:
            The Learner's ``WordEntry`` records, ordered by creation time
            (oldest first). An empty list when the collection is empty.
        """
        return list(
            self._db.execute(
                select(WordEntry)
                .where(WordEntry.user_id == user_id)
                .order_by(WordEntry.created_at)
            ).scalars()
        )
