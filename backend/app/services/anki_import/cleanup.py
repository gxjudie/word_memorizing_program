"""PURE Field_Cleanup and Furigana_Notation helpers (Req 2.23, 2.24, 2.25)."""
from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.anki_import.mapping import FieldMapping

_SOUND = re.compile(r"\[sound:[^\]]*\]")
_BREAK_TAG = re.compile(r"<\s*/?\s*(?:br|div|p|li)\b[^>]*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<!--.*?-->|<\s*/?\s*[A-Za-z][^>]*>", re.DOTALL)
_FURIGANA = re.compile(r"( ?)([^\s\[\]]+)\[([^\]]+)\]")  # (delimiter)(base)[reading]


def field_cleanup(value: str) -> str:
    """Field_Cleanup, with the steps in glossary order.

    (1) remove ``[sound:...]`` tags; (2) replace br/div/p/li tags with one
    space and remove every other tag; (3) decode HTML entities; (4) collapse
    each run of Unicode whitespace to one space; (5) trim.
    """
    s = _SOUND.sub("", value)
    s = _ANY_TAG.sub("", _BREAK_TAG.sub(" ", s))
    s = html.unescape(s)
    return " ".join(s.split())


def has_furigana(cleaned: str) -> bool:
    return _FURIGANA.search(cleaned) is not None


def furigana_word(cleaned: str) -> str:
    """Replace each ``base[reading]`` with ``base`` and drop its delimiter space (Req 2.24)."""
    return _FURIGANA.sub(r"\2", cleaned)


def furigana_reading(cleaned: str) -> str:
    """Replace each ``base[reading]`` with ``reading`` and drop its delimiter space (Req 2.25(b))."""
    return _FURIGANA.sub(r"\3", cleaned)


def _cleaned(values: Sequence[str], index: int | None) -> str:
    return field_cleanup(values[index]) if index is not None else ""


def resolve_note(values: Sequence[str], mapping: FieldMapping) -> tuple[str, str, str]:
    """Return ``(word_form, reading, meaning)`` for one note (Req 2.23-2.25).

    The reading precedence is (a) the non-empty cleaned mapped reading,
    (b) the furigana reading of the cleaned word value, (c) the word form.
    """
    word_raw = _cleaned(values, mapping.word)
    meaning = _cleaned(values, mapping.meaning)
    word = furigana_word(word_raw)
    reading_field = _cleaned(values, mapping.reading)
    if reading_field:
        reading = reading_field
    elif has_furigana(word_raw):
        reading = furigana_reading(word_raw)
    else:
        reading = word
    return word, reading, meaning
