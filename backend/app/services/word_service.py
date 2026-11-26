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
  to the current Study_Day (``current_study_day()``, 07:00 KST boundary), so
  it is immediately a Due_Word.

The service takes a SQLAlchemy ``Session`` and contains no HTTP concerns, so it
is testable without a web server (see the layer separation in the design).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models.word_entry import WordEntry
from app.schemas.word import WordInput
from app.services.study_day import current_study_day

__all__ = ["WordService", "DuplicateError", "WordNotFoundError"]


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


class WordNotFoundError(Exception):
    """Raised when a word id is unknown or belongs to another Learner.

    The two cases are deliberately indistinguishable, so one account cannot
    probe another account's word ids.

    Attributes:
        word_id: The word id that was not found for this Learner.
    """

    def __init__(self, word_id: str) -> None:
        self.word_id = word_id
        super().__init__(f"word not found: {word_id!r}")


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
            today: The current Study_Day, injected for testability. Defaults
                to ``current_study_day()`` (07:00 KST boundary) when omitted.

        Returns:
            The persisted ``WordEntry``, with ``next_review_date`` set to
            ``today`` so it is immediately a Due_Word (Req 1.5).

        Raises:
            DuplicateError: If an entry with the same ``word_form`` already
                exists for this user (Req 1.4). The existing entry is left
                unmodified and nothing is written.
        """
        creation_date = today if today is not None else current_study_day()

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

        entry = WordEntry(**self._entry_values(user_id, data, creation_date))
        self._db.add(entry)
        self._db.commit()
        self._db.refresh(entry)
        return entry

    def existing_word_forms(self, user_id: str) -> set[str]:
        """Return every word form in a Learner's collection (Req 2.29).

        The read runs in the session's current transaction, so the Anki import
        dedupes against the same snapshot it later inserts into.
        """
        return set(
            self._db.execute(
                select(WordEntry.word_form).where(WordEntry.user_id == user_id)
            ).scalars()
        )

    def add_words_bulk(
        self,
        user_id: str,
        inputs: Sequence[WordInput],
        today: date,
        chunk_size: int = 1000,
    ) -> int:
        """Insert many new Word_Entries without committing (Req 2.26, 2.30-2.32).

        Rows are inserted with bulk ``INSERT`` statements of at most
        ``chunk_size`` rows each. The caller owns the transaction: it commits
        once after every chunk succeeded, or rolls back so that none of the
        rows remain (Req 2.31). Dedupe is the caller's job; a word form that
        already exists raises ``IntegrityError`` from the unique constraint.

        Returns:
            The number of rows inserted.
        """
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        rows = [self._entry_values(user_id, data, today) for data in inputs]
        for start in range(0, len(rows), chunk_size):
            self._db.execute(insert(WordEntry), rows[start : start + chunk_size])
        return len(rows)

    @staticmethod
    def _entry_values(user_id: str, data: WordInput, today: date) -> dict[str, object]:
        """Column values of a new Word_Entry, due on ``today`` (Req 1.5, 2.26).

        Every new word starts with the default scheduling state and no review
        history, whether added by hand or imported (Req 2.30).
        """
        return {
            "user_id": user_id,
            "word_form": data.word_form,
            "reading": data.reading,
            "meaning": data.meaning,
            "next_review_date": today,  # due immediately
            "ease_factor": 2.5,
            "interval_days": 0,
            "repetitions": 0,
        }

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

    def get_word(self, user_id: str, word_id: str) -> WordEntry:
        """Return one owned Word_Entry or raise ``WordNotFoundError``."""
        entry = self._owned_entry(user_id, word_id)
        if entry is None:
            raise WordNotFoundError(word_id)
        return entry

    def update_word(
        self,
        user_id: str,
        word_id: str,
        data: WordInput,
    ) -> WordEntry:
        """Replace a word's lexical fields, keeping its review schedule.

        The scheduling state (``next_review_date``, ``ease_factor``,
        ``interval_days``, ``repetitions``) and the review history are left
        untouched: fixing a typo must not move the card to another day.

        Args:
            user_id: The owning Learner.
            word_id: The word to update.
            data: The already-validated replacement lexical fields.

        Returns:
            The updated ``WordEntry``.

        Raises:
            WordNotFoundError: The word is unknown or owned by someone else.
            DuplicateError: Another word in the same collection already uses the
                new word form. Nothing is written in that case.
        """
        entry = self.get_word(user_id, word_id)

        duplicate = self._db.execute(
            select(WordEntry).where(
                WordEntry.user_id == user_id,
                WordEntry.word_form == data.word_form,
                WordEntry.id != word_id,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise DuplicateError(data.word_form)

        entry.word_form = data.word_form
        entry.reading = data.reading
        entry.meaning = data.meaning
        self._db.commit()
        self._db.refresh(entry)
        return entry

    def delete_word(self, user_id: str, word_id: str) -> None:
        """Delete one owned word with its review history and sentences.

        The ORM cascades cover ``review_records`` and ``generated_sentences``;
        SQLite does not enforce ``ON DELETE CASCADE`` unless the foreign-key
        pragma is on, so the ORM-level cascade is what keeps local databases
        consistent.

        Raises:
            WordNotFoundError: The word is unknown or owned by someone else.
        """
        entry = self.get_word(user_id, word_id)
        self._db.delete(entry)
        self._db.commit()

    def _owned_entry(self, user_id: str, word_id: str) -> WordEntry | None:
        """Return the word when ``user_id`` owns it, else ``None``."""
        return self._db.execute(
            select(WordEntry).where(
                WordEntry.id == word_id,
                WordEntry.user_id == user_id,
            )
        ).scalar_one_or_none()
