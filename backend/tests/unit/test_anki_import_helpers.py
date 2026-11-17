"""Unit tests for the pure Anki import helpers (task 17.1).

Covers ``errors``, ``models``, ``file_kind``, ``cleanup``, and ``mapping`` in
``app.services.anki_import``.
"""
from __future__ import annotations

import pytest

from app.services.anki_import import errors
from app.services.anki_import.cleanup import (
    field_cleanup,
    furigana_reading,
    furigana_word,
    has_furigana,
    resolve_note,
)
from app.services.anki_import.file_kind import extension_of, file_kind_for, is_colpkg
from app.services.anki_import.mapping import (
    FieldMapping,
    Role,
    choose_default_note_type,
    suggest_mapping,
    validate_mapping,
)
from app.services.anki_import.models import AnkiNote, FileKind, NoteType, ParsedDeck

# ---------------------------------------------------------------- errors


ERROR_CODES = {
    errors.UnsupportedColpkgError: "unsupported_colpkg",
    errors.FileTooLargeError: "file_too_large",
    errors.UnsupportedExtensionError: "unsupported_extension",
    errors.UnreadableFileError: "unreadable_file",
    errors.UnsafePackageError: "unsafe_package",
    errors.InvalidTextExportError: "invalid_text_export",
    errors.UnrecognizedFormatError: "unrecognized_format",
    errors.NoNotesError: "no_notes",
    errors.InvalidNoteTypeError: "invalid_note_type",
    errors.PreviewExpiredError: "preview_expired",
    errors.ImportInProgressError: "import_in_progress",
    errors.ImportFailedError: "import_failed",
}


@pytest.mark.parametrize(("cls", "code"), ERROR_CODES.items())
def test_each_error_has_its_code_and_a_fixed_message(cls: type, code: str) -> None:
    err = cls()
    assert isinstance(err, errors.AnkiImportError)
    assert err.code == code
    assert err.message and str(err) == err.message
    assert cls().message == err.message


def test_messages_are_distinct_per_code() -> None:
    messages = [cls.message for cls in ERROR_CODES] + [errors.InvalidMappingError.message]
    assert len(set(messages)) == len(messages)


def test_invalid_mapping_error_carries_per_role_errors() -> None:
    err = errors.InvalidMappingError({Role.WORD: "missing", "meaning": "duplicate"})
    assert err.code == "invalid_mapping"
    assert dict(err.errors) == {"word": "missing", "meaning": "duplicate"}


def test_base_error_accepts_code_and_message() -> None:
    err = errors.AnkiImportError("custom", "Custom message.")
    assert (err.code, err.message, str(err)) == ("custom", "Custom message.", "Custom message.")


# ---------------------------------------------------------------- models


def _apkg_deck() -> ParsedDeck:
    a = NoteType(id="1", name="Basic", field_names=("Front", "Back"), note_count=1)
    return ParsedDeck(
        kind=FileKind.APKG,
        note_types=(a,),
        text_field_names=(),
        notes_by_type={"1": (AnkiNote(values=("猫", "cat")),)},
    )


def test_parsed_deck_looks_up_fields_and_notes_by_type() -> None:
    deck = _apkg_deck()
    assert deck.field_names("1") == ("Front", "Back")
    assert deck.notes("1") == (AnkiNote(values=("猫", "cat")),)
    with pytest.raises(errors.InvalidNoteTypeError):
        deck.field_names("2")
    with pytest.raises(errors.InvalidNoteTypeError):
        deck.notes(None)


def test_text_deck_uses_its_implicit_note_type() -> None:
    notes = (AnkiNote(values=("a", "b")),)
    deck = ParsedDeck(
        kind=FileKind.TEXT, note_types=(), text_field_names=("열 1", "열 2"),
        notes_by_type={None: notes},
    )
    assert deck.field_names(None) == ("열 1", "열 2")
    assert deck.notes(None) == notes


# ---------------------------------------------------------------- file kind


@pytest.mark.parametrize(
    ("name", "ext"),
    [
        ("deck.APKG", ".apkg"),
        ("my.deck.Txt", ".txt"),
        ("noext", ""),
        ("dir.v2/noext", ""),
        ("C:\\Users\\x\\deck.TSV", ".tsv"),
        ("a/b/deck.colpkg", ".colpkg"),
        (".apkg", ".apkg"),
    ],
)
def test_extension_of_uses_the_basename_case_insensitively(name: str, ext: str) -> None:
    assert extension_of(name) == ext


def test_is_colpkg_is_case_insensitive() -> None:
    assert is_colpkg("Deck.COLPKG")
    assert is_colpkg("x/y.ColPkg")
    assert not is_colpkg("deck.colpkg.apkg")
    assert not is_colpkg("colpkg")


@pytest.mark.parametrize(
    ("name", "kind"),
    [("d.apkg", FileKind.APKG), ("d.APKG", FileKind.APKG), ("d.txt", FileKind.TEXT),
     ("d.TSV", FileKind.TEXT)],
)
def test_file_kind_for_accepted_extensions(name: str, kind: FileKind) -> None:
    assert file_kind_for(name) is kind


@pytest.mark.parametrize("name", ["d.zip", "d.colpkg", "d", "d.csv", "apkg"])
def test_file_kind_for_rejects_other_extensions(name: str) -> None:
    with pytest.raises(errors.UnsupportedExtensionError):
        file_kind_for(name)


# ---------------------------------------------------------------- cleanup


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("猫[sound:neko.mp3]", "猫"),
        ("a<br>b<br/>c</div>d<p class='x'>e<LI>f", "a b c d e f"),
        ("<b>bold</b> <span style='x'>text</span>", "bold text"),
        ("x<!-- note -->y", "xy"),
        ("a&nbsp;&amp;&nbsp;b", "a & b"),
        ("&lt;b&gt;", "<b>"),
        ("  a \t\n\u3000 b  ", "a b"),
        ("a < b", "a < b"),
        ("<pre>x</pre>y", "xy"),
        ("", ""),
    ],
)
def test_field_cleanup(raw: str, cleaned: str) -> None:
    assert field_cleanup(raw) == cleaned


@pytest.mark.parametrize(
    ("value", "word", "reading"),
    [
        ("漢字[かんじ]", "漢字", "かんじ"),
        ("食[た]べる", "食べる", "たべる"),
        ("私[わたし]は 日本[にほん]", "私は日本", "わたしはにほん"),
        (" 日本[にほん]", "日本", "にほん"),
    ],
)
def test_furigana_word_and_reading(value: str, word: str, reading: str) -> None:
    assert has_furigana(value)
    assert furigana_word(value) == word
    assert furigana_reading(value) == reading


@pytest.mark.parametrize("value", ["漢字[]", "[かな]", "猫", "a b"])
def test_text_without_furigana_notation_is_unchanged(value: str) -> None:
    assert not has_furigana(value)
    assert furigana_word(value) == value
    assert furigana_reading(value) == value


def test_resolve_note_prefers_the_mapped_reading() -> None:
    values = ("<b>食[た]べる</b>", "たべる ", "to eat")
    assert resolve_note(values, FieldMapping(0, 1, 2)) == ("食べる", "たべる", "to eat")


def test_resolve_note_falls_back_to_furigana_when_reading_cleans_empty() -> None:
    values = ("食[た]べる", "<br>[sound:x.mp3]", "to eat")
    assert resolve_note(values, FieldMapping(0, 1, 2)) == ("食べる", "たべる", "to eat")


def test_resolve_note_falls_back_to_the_word_form() -> None:
    assert resolve_note(("猫", "cat"), FieldMapping(0, None, 1)) == ("猫", "猫", "cat")


def test_resolve_note_strips_furigana_without_a_reading_field() -> None:
    assert resolve_note(("漢字[かんじ]", "kanji"), FieldMapping(0, None, 1)) == (
        "漢字", "かんじ", "kanji",
    )


# ---------------------------------------------------------------- suggest_mapping


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["Front", "Back"], FieldMapping(0, None, 1)),
        (["  EXPRESSION ", "Meaning", "Reading"], FieldMapping(0, 2, 1)),
        (["単語", "読み", "意味"], FieldMapping(0, 1, 2)),
        (["단어", "읽기", "뜻"], FieldMapping(0, 1, 2)),
        (["a", "b", "c", "d"], FieldMapping(0, 1, 2)),
        (["a", "b"], FieldMapping(0, None, 1)),
        (["a"], FieldMapping(0, None, None)),
        ([], FieldMapping(None, None, None)),
        # Name match takes the lowest-position matching field; reading's
        # default (1) is taken by word, so reading becomes "없음".
        (["x", "Word", "Vocab", "Back"], FieldMapping(1, None, 3)),
        # Meaning matched at field 1, so word keeps 0 and reading's default (1) is taken.
        (["x", "Meaning", "y"], FieldMapping(0, None, 1)),
        # Meaning's default (2) is taken by word, so meaning takes the lowest free field.
        (["a", "b", "Word"], FieldMapping(2, 1, 0)),
        # Two fields matching word and reading leave no field for meaning.
        (["Word", "Reading"], FieldMapping(0, 1, None)),
        # One field named Meaning: word's default is taken and no field is free.
        (["Meaning"], FieldMapping(None, None, 0)),
    ],
)
def test_suggest_mapping(names: list[str], expected: FieldMapping) -> None:
    assert suggest_mapping(names) == expected


# ---------------------------------------------------------------- validate_mapping


@pytest.mark.parametrize(
    ("mapping", "n", "expected"),
    [
        (FieldMapping(0, 1, 2), 3, {}),
        (FieldMapping(0, None, 1), 2, {}),
        (FieldMapping(None, None, None), 3, {Role.WORD: "missing", Role.MEANING: "missing"}),
        (FieldMapping(0, None, 5), 3, {Role.MEANING: "missing"}),
        (FieldMapping(-1, None, 1), 3, {Role.WORD: "missing"}),
        (FieldMapping(0, 3, 1), 3, {Role.READING: "out_of_range"}),
        (FieldMapping(0, -1, 1), 3, {Role.READING: "out_of_range"}),
        (FieldMapping(0, None, 0), 2, {Role.WORD: "duplicate", Role.MEANING: "duplicate"}),
        (FieldMapping(1, 1, 1), 2,
         {Role.WORD: "duplicate", Role.READING: "duplicate", Role.MEANING: "duplicate"}),
        (FieldMapping(0, 0, 1), 2, {Role.WORD: "duplicate", Role.READING: "duplicate"}),
        (FieldMapping(0, 0, None), 1,
         {Role.WORD: "duplicate", Role.READING: "duplicate", Role.MEANING: "missing"}),
        (FieldMapping(0, None, 1), 0, {Role.WORD: "missing", Role.MEANING: "missing"}),
    ],
)
def test_validate_mapping(mapping: FieldMapping, n: int, expected: dict[Role, str]) -> None:
    assert validate_mapping(mapping, n) == expected


# ---------------------------------------------------------------- choose_default_note_type


def _nt(id_: str, name: str, count: int) -> NoteType:
    return NoteType(id=id_, name=name, field_names=("F",), note_count=count)


def test_default_note_type_has_the_most_notes() -> None:
    types = [_nt("1", "A", 2), _nt("2", "B", 5), _nt("3", "C", 1)]
    assert choose_default_note_type(types).id == "2"


def test_default_note_type_ties_break_by_code_point_name_then_id() -> None:
    types = [_nt("1", "기본", 3), _nt("2", "Basic", 3), _nt("3", "basic", 3)]
    assert choose_default_note_type(types).id == "2"  # "B" < "b" < Hangul
    assert choose_default_note_type([_nt("9", "X", 1), _nt("4", "X", 1)]).id == "4"


def test_default_note_type_requires_a_note_type() -> None:
    with pytest.raises(ValueError):
        choose_default_note_type([])
