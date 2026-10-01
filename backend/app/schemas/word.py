"""Request/response schemas for the Word_Collection (Req 1).

``WordInput`` is the add-word request body. Its validators enforce the length
bounds (Req 1.1, 1.3) and reject missing or whitespace-only fields (Req 1.2),
and they are written so that a submission with several offending fields reports
*each* offending field by name — the field-exact behavior required by
Property 2. ``WordEntryOut`` is the stored-record response shape.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator

# Per-field maximum lengths (Req 1.1 / 1.3).
WORD_FORM_MAX_LENGTH = 50
READING_MAX_LENGTH = 100
MEANING_MAX_LENGTH = 500


def _validate_text_field(value: str, *, max_length: int) -> str:
    """Validate and normalize a single required text field.

    Rejects a value that is missing / empty / whitespace-only (Req 1.2) or that
    exceeds ``max_length`` characters (Req 1.3). Length is measured against the
    original value so trailing/leading whitespace cannot be used to slip past
    the bound. On success the value is returned stripped of surrounding
    whitespace.

    Pydantic reports the raising field by name, so applying this per field
    yields an error naming exactly the offending field(s) (Property 2).
    """
    if value is None or value.strip() == "":
        raise ValueError("must not be empty or whitespace-only")
    if len(value) > max_length:
        raise ValueError(f"must be at most {max_length} characters")
    return value.strip()


class WordInput(BaseModel):
    """Add-word request body (Req 1.1-1.3).

    Attributes:
        word_form: Japanese word form, 1-50 non-whitespace characters.
        reading: Reading, 1-100 non-whitespace characters.
        meaning: Meaning, 1-500 non-whitespace characters.
    """

    word_form: str
    reading: str
    meaning: str

    @field_validator("word_form")
    @classmethod
    def _check_word_form(cls, value: str) -> str:
        return _validate_text_field(value, max_length=WORD_FORM_MAX_LENGTH)

    @field_validator("reading")
    @classmethod
    def _check_reading(cls, value: str) -> str:
        return _validate_text_field(value, max_length=READING_MAX_LENGTH)

    @field_validator("meaning")
    @classmethod
    def _check_meaning(cls, value: str) -> str:
        return _validate_text_field(value, max_length=MEANING_MAX_LENGTH)


class WordEntryOut(BaseModel):
    """Stored Word_Entry response shape.

    Mirrors the persisted record, including the scheduling state. A freshly
    created word has ``next_review_date`` set to its creation date so it is a
    Due_Word immediately (Req 1.5).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    word_form: str
    reading: str
    meaning: str
    next_review_date: date
    created_at: datetime
