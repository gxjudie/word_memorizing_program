"""Build Anki import fixtures in code, so no binary fixture is committed.

- ``build_legacy_apkg``: a schema-11 collection (``col.models`` JSON + ``notes``).
- ``build_anki21b_apkg``: a new-schema collection (``notetypes`` + ``fields`` +
  ``notes``), zstd-compressed as ``collection.anki21b``, plus a legacy
  ``collection.anki2`` stub holding a different single note.
- ``build_text_export``: an Anki plain-text export built on ``print_anki_text``.

The lower-level ``legacy_collection_bytes``, ``new_collection_bytes``, and
``zip_entries`` helpers let a test compose any package layout.

Field definitions are written in reverse ``ord`` order on purpose, so a reader
that ignores ``ord`` gets the wrong field order.
"""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import zstandard

from app.services.anki_import.text_reader import print_anki_text


@dataclass(frozen=True)
class NoteTypeSpec:
    id: int
    name: str
    field_names: Sequence[str]


@dataclass(frozen=True)
class NoteSpec:
    id: int
    mid: int
    values: Sequence[str]  # joined with \x1f into ``flds``


Entries = Sequence[tuple[str, bytes]]

STUB_NOTE_TYPE = NoteTypeSpec(id=1, name="Legacy stub", field_names=("Front", "Back"))
STUB_NOTE = NoteSpec(id=1, mid=1, values=("STUB", "legacy stub note"))

_NOTES_DDL = (
    "CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT NOT NULL, mid INTEGER NOT NULL,"
    " mod INTEGER NOT NULL, usn INTEGER NOT NULL, tags TEXT NOT NULL, flds TEXT NOT NULL,"
    " sfld INTEGER NOT NULL, csum INTEGER NOT NULL, flags INTEGER NOT NULL, data TEXT NOT NULL)"
)

_COL_DDL = (
    "CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER NOT NULL, mod INTEGER NOT NULL,"
    " scm INTEGER NOT NULL, ver INTEGER NOT NULL, dty INTEGER NOT NULL, usn INTEGER NOT NULL,"
    " ls INTEGER NOT NULL, conf TEXT NOT NULL, models TEXT NOT NULL, decks TEXT NOT NULL,"
    " dconf TEXT NOT NULL, tags TEXT NOT NULL)"
)


def _unicase(left: str, right: str) -> int:
    a, b = left.casefold(), right.casefold()
    return (a > b) - (a < b)


def _build_sqlite(populate, extra_sql: Sequence[str] = ()) -> bytes:
    with tempfile.TemporaryDirectory(prefix="jvt-fixture-") as tmp:
        db_path = Path(tmp) / "collection.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.create_collation("unicase", _unicase)
            populate(conn)
            for statement in extra_sql:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
        return db_path.read_bytes()


def _insert_notes(conn: sqlite3.Connection, notes: Sequence[NoteSpec]) -> None:
    conn.execute(_NOTES_DDL)
    conn.executemany(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, 0, 0, 0, '')",
        [
            (note.id, f"guid{note.id}", note.mid, "\x1f".join(note.values))
            for note in notes
        ],
    )


def legacy_models_json(note_types: Sequence[NoteTypeSpec]) -> str:
    """``col.models`` JSON with each note type's fields listed in reverse ``ord``."""
    models = {
        str(nt.id): {
            "id": nt.id,
            "name": nt.name,
            "flds": [
                {"name": name, "ord": ord_}
                for ord_, name in reversed(list(enumerate(nt.field_names)))
            ],
        }
        for nt in note_types
    }
    return json.dumps(models, ensure_ascii=False)


def legacy_collection_bytes(
    note_types: Sequence[NoteTypeSpec],
    notes: Sequence[NoteSpec],
    *,
    models_json: str | None = None,
    extra_sql: Sequence[str] = (),
) -> bytes:
    """A schema-11 SQLite collection file (uncompressed)."""
    models = legacy_models_json(note_types) if models_json is None else models_json

    def populate(conn: sqlite3.Connection) -> None:
        conn.execute(_COL_DDL)
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 11, 0, 0, 0, '{}', ?, '{}', '{}', '{}')",
            (models,),
        )
        _insert_notes(conn, notes)

    return _build_sqlite(populate, extra_sql)


def new_collection_bytes(
    note_types: Sequence[NoteTypeSpec],
    notes: Sequence[NoteSpec],
    *,
    extra_sql: Sequence[str] = (),
) -> bytes:
    """A new-schema (``notetypes`` + ``fields``) SQLite collection file, uncompressed."""

    def populate(conn: sqlite3.Connection) -> None:
        conn.execute(_COL_DDL)  # newer collections keep a ``col`` row with empty models
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '', '', '', '', '')"
        )
        conn.execute(
            "CREATE TABLE notetypes (id INTEGER NOT NULL PRIMARY KEY,"
            " name TEXT NOT NULL COLLATE unicase, mtime_secs INTEGER NOT NULL,"
            " usn INTEGER NOT NULL, config BLOB NOT NULL)"
        )
        conn.execute("CREATE UNIQUE INDEX idx_notetypes_name ON notetypes (name)")
        conn.execute(
            "CREATE TABLE fields (ntid INTEGER NOT NULL, ord INTEGER NOT NULL,"
            " name TEXT NOT NULL COLLATE unicase, config BLOB NOT NULL,"
            " PRIMARY KEY (ntid, ord)) WITHOUT ROWID"
        )
        conn.executemany(
            "INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')",
            [(nt.id, nt.name) for nt in note_types],
        )
        conn.executemany(
            "INSERT INTO fields VALUES (?, ?, ?, x'')",
            [
                (nt.id, ord_, name)
                for nt in note_types
                for ord_, name in reversed(list(enumerate(nt.field_names)))
            ],
        )
        _insert_notes(conn, notes)

    return _build_sqlite(populate, extra_sql)


def zstd_compress(data: bytes, *, write_content_size: bool = True) -> bytes:
    return zstandard.ZstdCompressor(write_content_size=write_content_size).compress(data)


def zip_entries(entries: Entries, *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    """A zip archive with the given ``(name, data)`` entries, in order."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as zf:
        for name, data in entries:
            zf.writestr(zipfile.ZipInfo(name), data, compress_type=compression)
    return buffer.getvalue()


def build_legacy_apkg(
    note_types: Sequence[NoteTypeSpec],
    notes: Sequence[NoteSpec],
    *,
    name: str = "collection.anki2",
    extra_entries: Entries = (),
    extra_sql: Sequence[str] = (),
) -> bytes:
    """A legacy ``.apkg``: the schema-11 collection under ``name``, ``media`` = ``{}``."""
    collection = legacy_collection_bytes(note_types, notes, extra_sql=extra_sql)
    return zip_entries([(name, collection), ("media", b"{}"), *extra_entries])


def build_anki21b_apkg(
    note_types: Sequence[NoteTypeSpec],
    notes: Sequence[NoteSpec],
    *,
    extra_entries: Entries = (),
    include_legacy_stub: bool = True,
    write_content_size: bool = True,
) -> bytes:
    """A new-format ``.apkg`` with a zstd ``collection.anki21b``.

    A legacy ``collection.anki2`` stub with ``STUB_NOTE`` is added by default,
    so a reader that ignores the priority order returns the wrong note.
    """
    collection = zstd_compress(
        new_collection_bytes(note_types, notes), write_content_size=write_content_size
    )
    entries: list[tuple[str, bytes]] = []
    if include_legacy_stub:
        entries.append(("collection.anki2", legacy_collection_bytes([STUB_NOTE_TYPE], [STUB_NOTE])))
    entries.append(("collection.anki21b", collection))
    entries.append(("media", b""))
    entries.extend(extra_entries)
    return zip_entries(entries)


def build_text_export(
    rows: Sequence[Sequence[str]],
    headers: Sequence[tuple[str, str]] = (),
) -> bytes:
    """An Anki plain-text export: ``#key:value`` header lines, then the printed rows."""
    header_text = "".join(f"#{key}:{value}\n" for key, value in headers)
    return (header_text + print_anki_text(rows)).encode("utf-8")
