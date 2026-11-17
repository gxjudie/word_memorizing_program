"""Anki plain-text export reader and printer (pure; Req 2.10-2.16).

``parse_anki_text`` is the Anki_Text_Parser: it reads an Anki "Notes in Plain
Text" export into its ``#key:value`` header lines and its rows of field values.
``print_anki_text`` is the Anki_Text_Printer: it writes rows in the same
tab-separated format, so that parsing the printed text gives the rows back
(Req 2.17). ``text_export_to_deck`` turns a parsed export into a TEXT
``ParsedDeck`` with named columns.

Nothing here touches I/O. Errors are the coded errors from ``errors.py``,
whose messages never echo file content.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.services.anki_import.errors import InvalidTextExportError, NoNotesError
from app.services.anki_import.models import AnkiNote, FileKind, ParsedDeck

_UTF8_BOM = b"\xef\xbb\xbf"

# An unquoted field (or the text after a closing quote) runs to the next TAB
# or LF. A CR right before the LF is part of a CRLF row end; any other CR is
# field content.
_FIELD_END = re.compile(r"[\t\n]")

# Characters that force the printer to quote a value (Req 2.16).
_NEEDS_QUOTES = re.compile(r'[\t\n\r"]')

# Headers that name a 1-based column to exclude from the available fields
# (Req 2.14).
_EXCLUDED_COLUMN_HEADERS = ("guid column", "notetype column", "deck column", "tags column")

_POSITIVE_INT = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class TextExport:
    headers: dict[str, str]  # lowercased, stripped key -> raw value (CR dropped)
    rows: list[list[str]]


# ---------------------------------------------------------------- parser


def parse_anki_text(data: bytes) -> TextExport:
    """Parse an Anki plain-text export (Anki_Text_Parser, Req 2.10, 2.11)."""
    if data.startswith(_UTF8_BOM):
        data = data[len(_UTF8_BOM):]
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise InvalidTextExportError() from None

    headers, pos = _read_headers(text)
    _check_separator(headers)
    return TextExport(headers=headers, rows=_read_rows(text, pos))


def _read_headers(text: str) -> tuple[dict[str, str], int]:
    """Read leading ``#key:value`` lines; return them and the first row offset."""
    headers: dict[str, str] = {}
    pos = 0
    length = len(text)
    while pos < length:
        end = text.find("\n", pos)
        next_pos = length if end == -1 else end + 1
        line = text[pos:length if end == -1 else end]
        if line.endswith("\r"):
            line = line[:-1]
        if line == "":
            pos = next_pos
            continue
        if not line.startswith("#"):
            break  # first data row; later '#' lines are data
        key, sep, value = line[1:].partition(":")
        if sep:
            headers[key.strip().lower()] = value
        pos = next_pos
    return headers, pos


def _check_separator(headers: dict[str, str]) -> None:
    separator = headers.get("separator")
    if separator is None:
        return
    value = separator.strip(" ")
    if value != "\t" and value.lower() != "tab":
        raise InvalidTextExportError()


def _read_rows(text: str, pos: int) -> list[list[str]]:
    rows: list[list[str]] = []
    length = len(text)
    while pos < length:
        # A line with zero characters (LF or CRLF only) is ignored.
        if text[pos] == "\n":
            pos += 1
            continue
        if text.startswith("\r\n", pos):
            pos += 2
            continue
        row, pos = _read_row(text, pos)
        rows.append(row)
    return rows


def _read_row(text: str, pos: int) -> tuple[list[str], int]:
    """Read one row starting at ``pos``; return it and the offset after its end."""
    row: list[str] = []
    length = len(text)
    while True:
        prefix = ""
        if pos < length and text[pos] == '"':
            prefix, pos = _read_quoted(text, pos + 1)
        # Unquoted field, or the literal remainder after a closing quote.
        match = _FIELD_END.search(text, pos)
        if match is None:
            row.append(prefix + text[pos:])
            return row, length
        end = match.start()
        if match.group() == "\t":
            row.append(prefix + text[pos:end])
            pos = end + 1
            continue
        # LF ends the row; a CR right before it belongs to the CRLF row end.
        content_end = end - 1 if end > pos and text[end - 1] == "\r" else end
        row.append(prefix + text[pos:content_end])
        return row, end + 1


def _read_quoted(text: str, pos: int) -> tuple[str, int]:
    """Read a quoted value whose opening quote precedes ``pos``.

    Returns the unescaped value and the offset just after the closing quote.
    """
    parts: list[str] = []
    length = len(text)
    while True:
        quote = text.find('"', pos)
        if quote == -1:
            raise InvalidTextExportError()  # end of file inside a quoted field
        parts.append(text[pos:quote])
        if quote + 1 < length and text[quote + 1] == '"':
            parts.append('"')
            pos = quote + 2
            continue
        return "".join(parts), quote + 1


# ---------------------------------------------------------------- printer


def print_anki_text(rows: Sequence[Sequence[str]]) -> str:
    """Format rows as Anki plain text (Anki_Text_Printer, Req 2.16)."""
    return "".join("\t".join(_format_value(value) for value in row) + "\n" for row in rows)


def _format_value(value: str) -> str:
    if value.startswith("#") or _NEEDS_QUOTES.search(value):
        return '"' + value.replace('"', '""') + '"'
    return value


# ---------------------------------------------------------------- deck shaping


def text_export_to_deck(export: TextExport) -> ParsedDeck:
    """Build a TEXT ``ParsedDeck`` with named columns (Req 2.12-2.15)."""
    if not export.rows:
        raise NoNotesError()

    columns_header = export.headers.get("columns")
    column_names = columns_header.split("\t") if columns_header is not None else []
    width = max(len(column_names), max(len(row) for row in export.rows))

    excluded: set[int] = set()
    for header in _EXCLUDED_COLUMN_HEADERS:
        position = _column_position(export.headers.get(header), width)
        if position is not None:
            excluded.add(position)

    positions = [p for p in range(1, width + 1) if p not in excluded]
    field_names = tuple(
        column_names[p - 1] if p <= len(column_names) else f"열 {p}" for p in positions
    )
    notes = tuple(
        AnkiNote(values=tuple(row[p - 1] if p <= len(row) else "" for p in positions))
        for row in export.rows
    )
    return ParsedDeck(
        kind=FileKind.TEXT,
        note_types=(),
        text_field_names=field_names,
        notes_by_type={None: notes},
    )


def _column_position(value: str | None, width: int) -> int | None:
    """A 1-based column position in ``1..width``, or ``None`` if not one."""
    if value is None:
        return None
    value = value.strip()
    if not _POSITIVE_INT.fullmatch(value):
        return None
    position = int(value)
    return position if 1 <= position <= width else None
