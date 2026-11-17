"""Request/response schemas for the Anki deck import (Requirement 2).

Fields are referenced by 0-based **index**, not by name, because two Anki
fields or text columns can share a name. Note type ids are strings because a
crafted package can hold ids beyond JavaScript's safe-integer range.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ImportNoteTypeOut(BaseModel):
    id: str
    name: str
    note_count: int


class ImportFieldOut(BaseModel):
    index: int  # 0-based position in the available fields
    name: str


class FieldMappingOut(BaseModel):
    word: int | None
    reading: int | None  # None = "없음"
    meaning: int | None


class ImportPreviewOut(BaseModel):
    preview_id: str
    file_kind: Literal["apkg", "text"]
    note_types: list[ImportNoteTypeOut]
    selected_note_type_id: str | None
    fields: list[ImportFieldOut]
    suggested_mapping: FieldMappingOut
    note_count: int
    samples: list[list[str]]  # <= 5 rows x len(fields), after Field_Cleanup (Req 2.5)
    expires_at: datetime  # UTC


class ConfirmImportIn(BaseModel):
    note_type_id: str | None = None
    mapping: FieldMappingOut  # the route parses it so shape errors become invalid_mapping


class SkippedNoteOut(BaseModel):
    word_form: str | None  # None when the word form is empty -> "(빈 단어)"
    reason: Literal["missing_field", "duplicate", "invalid_length"]


class ImportSummaryOut(BaseModel):
    created: int
    skipped_missing_field: int
    skipped_duplicate: int
    skipped_invalid_length: int
    skipped: list[SkippedNoteOut]  # first 20 skipped notes in file order (Req 2.34)
