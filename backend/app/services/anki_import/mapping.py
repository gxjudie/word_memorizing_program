"""PURE Field_Mapping suggestion and validation, and the default note type.

Requirements 2.8, 2.18, 2.19, 2.21, 2.22.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from app.services.anki_import.models import NoteType

# Stored in normalized (strip + casefold) form.
WORD_NAMES = frozenset({"front", "word", "expression", "vocab", "vocabulary", "単語", "단어"})
READING_NAMES = frozenset({"reading", "kana", "furigana", "yomi", "読み", "읽기"})
MEANING_NAMES = frozenset({"back", "meaning", "definition", "意味", "뜻", "의미"})


class Role(str, Enum):
    WORD = "word"
    READING = "reading"
    MEANING = "meaning"


_ROLE_ORDER = (Role.WORD, Role.READING, Role.MEANING)
_ROLE_NAMES = {Role.WORD: WORD_NAMES, Role.READING: READING_NAMES, Role.MEANING: MEANING_NAMES}


@dataclass(frozen=True)
class FieldMapping:
    word: int | None  # 0-based index into the available fields; None = no field
    reading: int | None  # None = "없음"
    meaning: int | None

    def index_of(self, role: Role) -> int | None:
        return getattr(self, role.value)


def _positional_defaults(n: int) -> dict[Role, int | None]:
    if n >= 3:
        return {Role.WORD: 0, Role.READING: 1, Role.MEANING: 2}
    if n == 2:
        return {Role.WORD: 0, Role.READING: None, Role.MEANING: 1}
    if n == 1:
        return {Role.WORD: 0, Role.READING: None, Role.MEANING: None}
    return {Role.WORD: None, Role.READING: None, Role.MEANING: None}


def suggest_mapping(field_names: Sequence[str]) -> FieldMapping:
    """Suggest a mapping: name match first (Req 2.18), then positions (Req 2.19)."""
    normalized = [name.strip().casefold() for name in field_names]
    n = len(normalized)

    chosen: dict[Role, int | None] = {}
    for role in _ROLE_ORDER:
        names = _ROLE_NAMES[role]
        match = next((i for i, name in enumerate(normalized) if name in names), None)
        if match is not None:
            chosen[role] = match
    taken = {index for index in chosen.values() if index is not None}

    defaults = _positional_defaults(n)
    for role in _ROLE_ORDER:
        if role in chosen:
            continue
        default = defaults[role]
        if default is None:
            index = None
        elif default not in taken:
            index = default
        elif role is Role.READING:
            index = None
        else:
            index = next((i for i in range(n) if i not in taken), None)
        chosen[role] = index
        if index is not None:
            taken.add(index)

    return FieldMapping(
        word=chosen[Role.WORD], reading=chosen[Role.READING], meaning=chosen[Role.MEANING]
    )


def validate_mapping(mapping: FieldMapping, field_count: int) -> dict[Role, str]:
    """Return ``{role: reason}`` for every invalid role; empty when valid.

    Reasons: ``missing`` (word or meaning is None or out of range),
    ``out_of_range`` (reading set but out of range), ``duplicate`` (an
    in-range index shared with another role, recorded for every sharer).
    """
    def in_range(index: int | None) -> bool:
        return index is not None and 0 <= index < field_count

    errors: dict[Role, str] = {}
    for role in _ROLE_ORDER:
        index = mapping.index_of(role)
        if role is Role.READING:
            if index is not None and not in_range(index):
                errors[role] = "out_of_range"
        elif not in_range(index):
            errors[role] = "missing"

    for role in _ROLE_ORDER:
        index = mapping.index_of(role)
        if role in errors or not in_range(index):
            continue
        if any(other is not role and mapping.index_of(other) == index for other in _ROLE_ORDER):
            errors[role] = "duplicate"

    return {role: errors[role] for role in _ROLE_ORDER if role in errors}


def choose_default_note_type(note_types: Sequence[NoteType]) -> NoteType:
    """The type with the most notes; ties go to the smallest name, then id (Req 2.8)."""
    if not note_types:
        raise ValueError("no note types to choose from")
    return min(note_types, key=lambda t: (-t.note_count, t.name, t.id))
