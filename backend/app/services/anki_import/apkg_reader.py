"""Anki ``.apkg`` package reader (Req 2.3, 2.5, 2.7, 2.8, 2.30, 2.39-2.41).

A ``.apkg`` is a zip archive holding one SQLite collection file plus media.
The reader:

1. opens the zip and rejects it before opening any entry when an entry name
   is unsafe (Req 2.41);
2. picks the newest collection file present (Req 2.7) and never opens media
   entries or ``meta`` (Req 2.30, 2.40);
3. caps the collection's uncompressed size at 200 MiB, both from the declared
   sizes and while streaming it to the workdir (Req 2.41);
4. opens the extracted SQLite file read-only, as data only (Req 2.39);
5. reads note types and notes from either the new (``notetypes`` + ``fields``)
   or the legacy (``col.models`` JSON) schema.

Every library exception is re-raised as a coded ``AnkiImportError`` ``from
None``, so no file content, entry name, or SQLite error text reaches a
response.
"""
from __future__ import annotations

import json
import re
import sqlite3
import zipfile
import zlib
from collections.abc import Iterable
from pathlib import Path
from urllib.request import pathname2url

import zstandard

from app.services.anki_import.errors import (
    AnkiImportError,
    NoNotesError,
    UnreadableFileError,
    UnrecognizedFormatError,
    UnsafePackageError,
)
from app.services.anki_import.models import AnkiNote, FileKind, NoteType, ParsedDeck

COLLECTION_PRIORITY = ("collection.anki21b", "collection.anki21", "collection.anki2")  # Req 2.7
MAX_COLLECTION_BYTES = 200 * 1024 * 1024  # Req 2.41

COLLECTION_FILENAME = "collection.sqlite"
_ZSTD_COLLECTION = "collection.anki21b"
_ZSTD_HEADER_BYTES = 18  # the largest zstd frame header
_CHUNK_BYTES = 1024 * 1024
_FIELD_SEPARATOR = "\x1f"

_DRIVE_PREFIX = re.compile(r"[A-Za-z]:")

# Errors raised while reading or decompressing a zip entry.
_EXTRACT_ERRORS = (
    zipfile.BadZipFile,  # includes "Bad CRC-32"
    zlib.error,
    zstandard.ZstdError,
    EOFError,
    NotImplementedError,  # unsupported compression method
    RuntimeError,  # zipfile's "File is encrypted" and similar
    OSError,
)

# Authorizer actions the collection connection may perform (Req 2.39).
_ALLOWED_ACTIONS = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION})


# ---------------------------------------------------------------- entry names (pure)


def is_unsafe_entry_name(name: str) -> bool:
    """True when a zip entry name could escape or confuse the extraction root.

    Unsafe: starts with ``/``, starts with a drive prefix such as ``C:``,
    contains a backslash, or has a ``..`` segment after splitting on ``/``.
    """
    if name.startswith("/") or "\\" in name or _DRIVE_PREFIX.match(name):
        return True
    return ".." in name.split("/")


def check_entry_names(names: Iterable[str]) -> None:
    """Raise ``UnsafePackageError`` if any entry name is unsafe (Req 2.41)."""
    for name in names:
        if is_unsafe_entry_name(name):
            raise UnsafePackageError()


# ---------------------------------------------------------------- package reader


def read_apkg(
    path: Path,
    workdir: Path,
    *,
    max_collection_bytes: int = MAX_COLLECTION_BYTES,
) -> ParsedDeck:
    """Read a ``.apkg`` package into a ``ParsedDeck``.

    Only the selected collection file is extracted, to
    ``workdir/collection.sqlite``. The caller owns ``workdir`` and deletes it.
    """
    db_path = Path(workdir) / COLLECTION_FILENAME
    _extract_collection(Path(path), db_path, max_collection_bytes)
    return _read_collection(db_path)


def _extract_collection(path: Path, db_path: Path, max_bytes: int) -> None:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError):
        raise UnreadableFileError() from None
    with zf:
        try:
            infos = zf.infolist()  # central directory only; no entry is opened
        except (zipfile.BadZipFile, OSError, ValueError):
            raise UnreadableFileError() from None
        check_entry_names(info.filename for info in infos)
        if any(info.flag_bits & 0x1 for info in infos):
            raise UnreadableFileError()  # encrypted entry

        by_name = {info.filename: info for info in infos}
        name = next((n for n in COLLECTION_PRIORITY if n in by_name), None)
        if name is None:
            raise UnrecognizedFormatError()
        info = by_name[name]
        if info.file_size > max_bytes:
            raise UnsafePackageError()

        is_zstd = name == _ZSTD_COLLECTION
        expected_size = _zstd_content_size(zf, info, max_bytes) if is_zstd else None
        _stream_to_file(zf, info, db_path, max_bytes, is_zstd, expected_size)


def _zstd_content_size(zf: zipfile.ZipFile, info: zipfile.ZipInfo, max_bytes: int) -> int | None:
    """The frame content size from the zstd header, or ``None`` when unknown."""
    try:
        with zf.open(info) as raw:
            header = raw.read(_ZSTD_HEADER_BYTES)
        size = zstandard.frame_content_size(header)
    except _EXTRACT_ERRORS:
        raise UnreadableFileError() from None
    if size < 0:
        return None  # the frame does not declare its size; the stream count guards
    if size > max_bytes:
        raise UnsafePackageError()
    return size


def _stream_to_file(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    db_path: Path,
    max_bytes: int,
    is_zstd: bool,
    expected_size: int | None,
) -> None:
    written = 0
    try:
        with zf.open(info) as raw, open(db_path, "wb") as out:
            source = zstandard.ZstdDecompressor().stream_reader(raw) if is_zstd else raw
            try:
                while chunk := source.read(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > max_bytes:
                        raise UnsafePackageError()  # a header understated the size
                    out.write(chunk)
            finally:
                if source is not raw:
                    source.close()
        if written == 0 or (expected_size is not None and written != expected_size):
            raise UnreadableFileError()  # empty or truncated collection
    except AnkiImportError:
        db_path.unlink(missing_ok=True)
        raise
    except _EXTRACT_ERRORS:
        db_path.unlink(missing_ok=True)
        raise UnreadableFileError() from None


# ---------------------------------------------------------------- SQLite (data only)


def _unicase(left: str, right: str) -> int:
    """Application-defined stand-in for Anki's ``unicase`` collation."""
    a, b = left.casefold(), right.casefold()
    return (a > b) - (a < b)


def _authorize(action: int, *_args: object) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def open_collection_readonly(db_path: Path) -> sqlite3.Connection:
    """Open an extracted collection read-only, executing nothing from the file.

    Extension loading is never enabled. ``query_only`` and ``trusted_schema =
    OFF`` are set, then an authorizer limits statements to SELECT reads and
    function calls.
    """
    uri = f"file:{pathname2url(str(db_path))}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        conn.create_collation("unicase", _unicase)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.set_authorizer(_authorize)
    except BaseException:
        conn.close()
        raise
    return conn


def _read_collection(db_path: Path) -> ParsedDeck:
    try:
        conn = open_collection_readonly(db_path)
    except sqlite3.Error:
        raise UnreadableFileError() from None
    try:
        return _read_deck(conn)
    except sqlite3.Error:
        raise UnreadableFileError() from None
    finally:
        conn.close()


def _read_deck(conn: sqlite3.Connection) -> ParsedDeck:
    tables = {
        name
        for kind, name in conn.execute("SELECT type, name FROM sqlite_master")
        if kind == "table"
    }
    if "notes" not in tables:
        raise UnrecognizedFormatError()
    if {"notetypes", "fields"} <= tables:
        type_defs = _read_new_note_types(conn)
    elif "col" in tables:
        type_defs = _read_legacy_note_types(conn)
    else:
        raise UnrecognizedFormatError()
    if not type_defs:
        raise UnrecognizedFormatError()

    notes_by_type: dict[str, list[AnkiNote]] = {type_id: [] for type_id in type_defs}
    for _note_id, mid, flds in conn.execute("SELECT id, mid, flds FROM notes ORDER BY id"):
        type_id = str(mid)
        if type_id not in type_defs:
            continue  # no field names for this note
        if not isinstance(flds, str):
            raise UnreadableFileError()
        field_count = len(type_defs[type_id][1])
        values = flds.split(_FIELD_SEPARATOR)
        values = (values + [""] * field_count)[:field_count]
        notes_by_type[type_id].append(AnkiNote(values=tuple(values)))

    note_types = tuple(
        NoteType(id=type_id, name=name, field_names=fields, note_count=len(notes_by_type[type_id]))
        for type_id, (name, fields) in type_defs.items()
        if notes_by_type[type_id]
    )
    if not note_types:
        raise NoNotesError()
    return ParsedDeck(
        kind=FileKind.APKG,
        note_types=note_types,
        text_field_names=(),
        notes_by_type={nt.id: tuple(notes_by_type[nt.id]) for nt in note_types},
    )


_NoteTypeDefs = dict[str, tuple[str, tuple[str, ...]]]  # id -> (name, field names)


def _read_new_note_types(conn: sqlite3.Connection) -> _NoteTypeDefs:
    names: dict[str, str] = {}
    for type_id, name in conn.execute("SELECT id, name FROM notetypes ORDER BY id"):
        if not isinstance(name, str):
            raise UnreadableFileError()
        names[str(type_id)] = name
    fields: dict[str, list[str]] = {type_id: [] for type_id in names}
    for ntid, _ord, name in conn.execute("SELECT ntid, ord, name FROM fields ORDER BY ntid, ord"):
        type_id = str(ntid)
        if type_id not in fields:
            continue
        if not isinstance(name, str):
            raise UnreadableFileError()
        fields[type_id].append(name)
    return {type_id: (name, tuple(fields[type_id])) for type_id, name in names.items()}


def _read_legacy_note_types(conn: sqlite3.Connection) -> _NoteTypeDefs:
    row = conn.execute("SELECT models FROM col LIMIT 1").fetchone()
    if row is None or not isinstance(row[0], str):
        raise UnreadableFileError()
    try:
        models = json.loads(row[0])
        if not isinstance(models, dict):
            raise TypeError
        result: _NoteTypeDefs = {}
        for type_id, model in models.items():
            name = model["name"]
            flds = model["flds"]
            if not isinstance(name, str) or not isinstance(flds, list):
                raise TypeError
            ordered = sorted(flds, key=_field_ord)
            field_names = tuple(_field_name(field) for field in ordered)
            result[str(type_id)] = (name, field_names)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise UnreadableFileError() from None
    return result


def _field_ord(field: object) -> int:
    if not isinstance(field, dict):
        raise TypeError
    ord_ = field["ord"]
    if isinstance(ord_, bool) or not isinstance(ord_, int):
        raise TypeError
    return ord_


def _field_name(field: dict) -> str:
    name = field["name"]
    if not isinstance(name, str):
        raise TypeError
    return name
