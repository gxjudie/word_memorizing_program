"""In-memory data parsed from an Anki_Deck_File (no table, no migration)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from app.services.anki_import.errors import InvalidNoteTypeError


class FileKind(str, Enum):
    APKG = "apkg"
    TEXT = "text"


@dataclass(frozen=True)
class NoteType:
    """A Note_Type of a ``.apkg`` file that has at least one note."""

    id: str  # Anki note type id as a string (may exceed 2^53)
    name: str
    field_names: tuple[str, ...]  # in the note type's field order (Req 2.8)
    note_count: int  # >= 1; types without notes are not listed


@dataclass(frozen=True)
class AnkiNote:
    values: tuple[str, ...]  # raw (uncleaned) values, one per available field


@dataclass(frozen=True)
class ParsedDeck:
    """A parsed deck.

    ``notes_by_type`` maps a note type id to its notes in ascending note id
    order for APKG, and ``None`` to the notes in data-row order for TEXT.
    """

    kind: FileKind
    note_types: tuple[NoteType, ...]  # () for TEXT
    text_field_names: tuple[str, ...]  # available fields for TEXT; () for APKG
    notes_by_type: Mapping[str | None, tuple[AnkiNote, ...]]

    def _note_type(self, note_type_id: str | None) -> NoteType:
        for note_type in self.note_types:
            if note_type.id == note_type_id:
                return note_type
        raise InvalidNoteTypeError()

    def field_names(self, note_type_id: str | None) -> tuple[str, ...]:
        """Available fields; a text deck has one implicit type and ignores the id."""
        if self.kind is FileKind.TEXT:
            return self.text_field_names
        return self._note_type(note_type_id).field_names

    def notes(self, note_type_id: str | None) -> tuple[AnkiNote, ...]:
        """Notes of the given type in file order (Req 2.5, 2.9)."""
        if self.kind is FileKind.TEXT:
            return self.notes_by_type.get(None, ())
        note_type = self._note_type(note_type_id)
        return self.notes_by_type.get(note_type.id, ())
