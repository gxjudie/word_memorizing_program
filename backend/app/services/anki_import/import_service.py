"""Import planning and the Anki import service (Req 2.4-2.9, 2.21-2.37, 2.42).

``plan_import`` is pure: it decides, note by note in file order, whether a
note becomes a Word_Entry or is skipped and why. ``AnkiImportService`` wires
the readers, the ``PreviewStore``, and the ``WordService`` together; its
``confirm`` writes every planned word in one transaction and commits once, so
a failure leaves the Word_Collection unchanged (Req 2.31).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, date
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.schemas.word import (
    MEANING_MAX_LENGTH,
    READING_MAX_LENGTH,
    WORD_FORM_MAX_LENGTH,
    WordInput,
)
from app.schemas.word_import import (
    ConfirmImportIn,
    FieldMappingOut,
    ImportFieldOut,
    ImportNoteTypeOut,
    ImportPreviewOut,
    ImportSummaryOut,
    SkippedNoteOut,
)
from app.services.anki_import.apkg_reader import read_apkg
from app.services.anki_import.cleanup import field_cleanup, resolve_note
from app.services.anki_import.errors import (
    ImportFailedError,
    InvalidMappingError,
    InvalidNoteTypeError,
)
from app.services.anki_import.file_kind import file_kind_for
from app.services.anki_import.mapping import FieldMapping, suggest_mapping, validate_mapping
from app.services.anki_import.models import FileKind
from app.services.anki_import.preview_store import PreviewRecord, PreviewStore
from app.services.anki_import.text_reader import parse_anki_text, text_export_to_deck
from app.services.word_service import WordService

if TYPE_CHECKING:
    from app.api.upload import ReceivedFile

SAMPLE_NOTE_COUNT = 5  # Req 2.5
SUMMARY_SKIPPED_LIMIT = 20  # Req 2.34


class SkipReason(str, Enum):
    MISSING_FIELD = "missing_field"
    INVALID_LENGTH = "invalid_length"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class SkippedNote:
    word_form: str | None  # None when the word form is empty -> UI shows "(빈 단어)"
    reason: SkipReason


@dataclass(frozen=True)
class ImportPlan:
    to_create: list[WordInput]  # in file order
    skipped: list[SkippedNote]  # every skipped note, in file order


def plan_import(
    notes: Iterable[Sequence[str]],
    mapping: FieldMapping,
    existing_forms: AbstractSet[str],
) -> ImportPlan:
    """Decide the outcome of every note, in file order (Req 2.23-2.29).

    Each note is resolved with ``resolve_note`` and gets the first matching
    rule: missing field, then invalid length (code points; nothing is
    truncated), then duplicate (an existing form or one planned earlier in
    this import), otherwise a ``WordInput``.
    """
    to_create: list[WordInput] = []
    skipped: list[SkippedNote] = []
    planned: set[str] = set()
    for values in notes:
        word, reading, meaning = resolve_note(values, mapping)
        if word == "" or meaning == "":
            skipped.append(SkippedNote(word or None, SkipReason.MISSING_FIELD))
        elif (
            len(word) > WORD_FORM_MAX_LENGTH
            or len(reading) > READING_MAX_LENGTH
            or len(meaning) > MEANING_MAX_LENGTH
        ):
            skipped.append(SkippedNote(word, SkipReason.INVALID_LENGTH))
        elif word in existing_forms or word in planned:
            skipped.append(SkippedNote(word, SkipReason.DUPLICATE))
        else:
            # The reading is non-empty here: Req 2.25(c) falls back to the word.
            to_create.append(WordInput(word_form=word, reading=reading, meaning=meaning))
            planned.add(word)
    return ImportPlan(to_create=to_create, skipped=skipped)


def build_preview(record: PreviewRecord) -> ImportPreviewOut:
    """The Import_Preview DTO for the record's selected note type (Req 2.5, 2.8, 2.9)."""
    deck = record.deck
    note_type_id = record.selected_note_type_id
    names = deck.field_names(note_type_id)
    notes = deck.notes(note_type_id)
    suggested = suggest_mapping(names)
    expires_at = record.expires_at
    if expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(UTC)
    return ImportPreviewOut(
        preview_id=record.preview_id,
        file_kind=deck.kind.value,
        note_types=[
            ImportNoteTypeOut(id=nt.id, name=nt.name, note_count=nt.note_count)
            for nt in deck.note_types
        ],
        selected_note_type_id=note_type_id,
        fields=[ImportFieldOut(index=i, name=name) for i, name in enumerate(names)],
        suggested_mapping=FieldMappingOut(
            word=suggested.word, reading=suggested.reading, meaning=suggested.meaning
        ),
        note_count=len(notes),
        samples=[
            [field_cleanup(value) for value in note.values]
            for note in notes[:SAMPLE_NOTE_COUNT]
        ],
        expires_at=expires_at,
    )


def _summary(created: int, skipped: Sequence[SkippedNote]) -> ImportSummaryOut:
    def count(reason: SkipReason) -> int:
        return sum(1 for note in skipped if note.reason is reason)

    return ImportSummaryOut(
        created=created,
        skipped_missing_field=count(SkipReason.MISSING_FIELD),
        skipped_duplicate=count(SkipReason.DUPLICATE),
        skipped_invalid_length=count(SkipReason.INVALID_LENGTH),
        skipped=[
            SkippedNoteOut(word_form=note.word_form or None, reason=note.reason.value)
            for note in skipped[:SUMMARY_SKIPPED_LIMIT]
        ],
    )


class AnkiImportService:
    """Preview, confirm, and cancel an Anki import for one Learner (Req 2.37)."""

    def __init__(
        self,
        db: Session,
        store: PreviewStore,
        words: WordService,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._db = db
        self._store = store
        self._words = words
        self._today = today

    def create_preview(
        self, user_id: str, received: ReceivedFile, workdir: Path
    ) -> ImportPreviewOut:
        """Parse the uploaded file and store a new Import_Preview (Req 2.4, 2.5).

        Blocking work; the route runs it in the threadpool. The caller owns
        ``workdir`` and deletes it on every path.
        """
        kind = file_kind_for(received.filename)
        if kind is FileKind.APKG:
            deck = read_apkg(received.path, workdir)
        else:
            deck = text_export_to_deck(parse_anki_text(Path(received.path).read_bytes()))
        record = self._store.add(user_id, received.filename, deck)
        return build_preview(record)

    def get_preview(
        self, user_id: str, preview_id: str, note_type_id: str | None = None
    ) -> ImportPreviewOut:
        """The preview, optionally switched to another note type (Req 2.9)."""
        if note_type_id is not None:
            record = self._store.select_note_type(preview_id, user_id, note_type_id)
        else:
            record = self._store.get_pending(preview_id, user_id)
        return build_preview(record)

    def confirm(
        self, user_id: str, preview_id: str, body: ConfirmImportIn
    ) -> ImportSummaryOut:
        """Run the import and return the Import_Summary (Req 2.21-2.34, 2.42).

        Raises, in check order: ``PreviewExpiredError``, ``InvalidNoteTypeError``,
        ``InvalidMappingError`` (preview stays pending), ``ImportInProgressError``
        (preview stays pending), then ``ImportFailedError`` after a rollback.
        """
        record = self._store.get_pending(preview_id, user_id)
        deck = record.deck

        note_type_id: str | None = None
        if deck.kind is FileKind.APKG:
            note_type_id = body.note_type_id or record.selected_note_type_id
            if not any(nt.id == note_type_id for nt in deck.note_types):
                raise InvalidNoteTypeError()

        mapping = FieldMapping(
            word=body.mapping.word, reading=body.mapping.reading, meaning=body.mapping.meaning
        )
        errors = validate_mapping(mapping, len(deck.field_names(note_type_id)))
        if errors:
            raise InvalidMappingError(errors)

        with self._store.begin_import(preview_id, user_id) as importing:
            try:
                existing = self._words.existing_word_forms(user_id)
                plan = plan_import(
                    (note.values for note in importing.deck.notes(note_type_id)),
                    mapping,
                    existing,
                )
                created = self._words.add_words_bulk(user_id, plan.to_create, self._today())
                self._db.commit()
            except Exception:
                try:
                    self._db.rollback()
                except Exception:  # pragma: no cover - the original failure wins
                    pass
                raise ImportFailedError() from None
        return _summary(created, plan.skipped)

    def cancel(self, user_id: str, preview_id: str) -> None:
        """Discard the preview; owner-only and idempotent (Req 2.35)."""
        self._store.cancel(preview_id, user_id)
