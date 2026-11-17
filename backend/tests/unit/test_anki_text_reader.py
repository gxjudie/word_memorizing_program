"""Unit tests for the Anki plain-text reader and printer (task 17.7).

Covers ``parse_anki_text``, ``print_anki_text``, and ``text_export_to_deck`` in
``app.services.anki_import.text_reader``.
"""
from __future__ import annotations

import pytest

from app.services.anki_import.errors import InvalidTextExportError, NoNotesError
from app.services.anki_import.models import FileKind
from app.services.anki_import.text_reader import (
    TextExport,
    parse_anki_text,
    print_anki_text,
    text_export_to_deck,
)

# ---------------------------------------------------------------- parser


def test_parse_basic_rows_with_bom_crlf_and_headers() -> None:
    data = "\ufeff#separator:tab\r\n#html:true\r\n\r\n食べる\tたべる\tto eat\r\n飲む\tのむ\r\n".encode()
    export = parse_anki_text(data)
    assert export.headers == {"separator": "tab", "html": "true"}
    assert export.rows == [["食べる", "たべる", "to eat"], ["飲む", "のむ"]]


def test_header_keys_are_lowercased_and_stripped_and_lines_without_colon_skipped() -> None:
    export = parse_anki_text(b"# Columns :Front\tBack\n#no colon here\nA\tB\n")
    assert export.headers == {"columns": "Front\tBack"}
    assert export.rows == [["A", "B"]]


def test_hash_lines_after_first_data_row_are_data() -> None:
    export = parse_anki_text(b"a\tb\n#not:header\n")
    assert export.headers == {}
    assert export.rows == [["a", "b"], ["#not:header"]]


def test_empty_lines_ignored_and_tab_only_line_is_row_of_empty_values() -> None:
    export = parse_anki_text(b"\n\na\n\r\n\t\t\nb")
    assert export.rows == [["a"], ["", "", ""], ["b"]]


def test_lone_cr_is_content() -> None:
    export = parse_anki_text(b"a\rb\tc\r\n")
    assert export.rows == [["a\rb", "c"]]


def test_quoted_field_holds_tab_line_breaks_and_doubled_quotes() -> None:
    export = parse_anki_text(b'"x\ty\r\nz ""q"""\tplain\n"#tag"\n')
    assert export.rows == [['x\ty\r\nz "q"', "plain"], ["#tag"]]


def test_text_after_closing_quote_is_appended_literally() -> None:
    export = parse_anki_text(b'"ab"cd\te\r\n')
    assert export.rows == [["abcd", "e"]]


def test_quote_inside_unquoted_field_is_literal() -> None:
    export = parse_anki_text(b'a"b\tc\n')
    assert export.rows == [['a"b', "c"]]


@pytest.mark.parametrize("value", ["tab", "Tab", " TAB ", "\t", " \t "])
def test_tab_separator_values_are_accepted(value: str) -> None:
    export = parse_anki_text(f"#separator:{value}\na\tb\n".encode())
    assert export.rows == [["a", "b"]]


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe a\tb\n",  # not UTF-8
        b"#separator:comma\na,b\n",
        b"#separator:,\na,b\n",
        b'a\t"unclosed\nmore\n',
        b'"x""\n',  # the doubled quote leaves the field open
    ],
)
def test_invalid_text_exports_raise(data: bytes) -> None:
    with pytest.raises(InvalidTextExportError) as excinfo:
        parse_anki_text(data)
    assert excinfo.value.code == "invalid_text_export"


# ---------------------------------------------------------------- printer


def test_printer_quotes_only_when_needed() -> None:
    rows = [["plain", "a\tb", "line\nbreak", 'say "hi"', "#hash", "cr\r", "mid#hash"]]
    assert print_anki_text(rows) == (
        'plain\t"a\tb"\t"line\nbreak"\t"say ""hi"""\t"#hash"\t"cr\r"\tmid#hash\n'
    )


def test_printer_writes_lf_row_ends_and_no_headers() -> None:
    assert print_anki_text([["a", "b"], ["c", ""]]) == "a\tb\nc\t\n"
    assert print_anki_text([]) == ""


def test_printer_output_parses_back() -> None:
    rows = [
        ["#先頭", 'a "b"\tc', "x\r\ny"],
        ["", "\r", "漢字[かんじ]"],
        ["  ", "", '"'],
    ]
    for line_end in ("\n", "\r\n"):
        # Only row-ending LFs become CRLF; LFs inside quoted values stay.
        printed = "".join(print_anki_text([row])[:-1] + line_end for row in rows)
        for prefix in (b"", b"\xef\xbb\xbf"):
            assert parse_anki_text(prefix + printed.encode()).rows == rows


# ---------------------------------------------------------------- deck shaping


def test_deck_without_columns_header_names_columns_by_position_and_pads() -> None:
    deck = text_export_to_deck(TextExport(headers={}, rows=[["a", "b", "c"], ["d"]]))
    assert deck.kind is FileKind.TEXT
    assert deck.note_types == ()
    assert deck.field_names(None) == ("열 1", "열 2", "열 3")
    assert [n.values for n in deck.notes(None)] == [("a", "b", "c"), ("d", "", "")]


def test_deck_columns_header_names_and_extra_columns() -> None:
    export = TextExport(headers={"columns": "Front\tBack"}, rows=[["a", "b", "c"]])
    assert text_export_to_deck(export).field_names(None) == ("Front", "Back", "열 3")


def test_deck_columns_header_wider_than_rows() -> None:
    export = TextExport(headers={"columns": "Front\tReading\tBack"}, rows=[["a"]])
    deck = text_export_to_deck(export)
    assert deck.field_names(None) == ("Front", "Reading", "Back")
    assert deck.notes(None)[0].values == ("a", "", "")


def test_deck_excludes_metadata_columns_and_ignores_bad_positions() -> None:
    export = TextExport(
        headers={
            "guid column": "1",
            "notetype column": " 2 ",
            "deck column": "9",  # out of range
            "tags column": "x",  # not an integer
        },
        rows=[["g", "nt", "word", "meaning", "tags"]],
    )
    deck = text_export_to_deck(export)
    assert deck.field_names(None) == ("열 3", "열 4", "열 5")
    assert deck.notes(None)[0].values == ("word", "meaning", "tags")


def test_deck_with_zero_rows_raises_no_notes() -> None:
    with pytest.raises(NoNotesError):
        text_export_to_deck(parse_anki_text(b"#separator:tab\n#html:false\n"))
