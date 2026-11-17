"""Unit tests for the Anki ``.apkg`` package reader (task 17.10).

All packages are built in code by ``tests/fixtures/anki_builders.py``.
"""
from __future__ import annotations

import sqlite3
import zipfile
from itertools import combinations
from pathlib import Path

import pytest

from app.services.anki_import import apkg_reader
from app.services.anki_import.apkg_reader import (
    COLLECTION_FILENAME,
    COLLECTION_PRIORITY,
    MAX_COLLECTION_BYTES,
    check_entry_names,
    is_unsafe_entry_name,
    open_collection_readonly,
    read_apkg,
)
from app.services.anki_import.errors import (
    NoNotesError,
    UnreadableFileError,
    UnrecognizedFormatError,
    UnsafePackageError,
)
from app.services.anki_import.models import FileKind
from tests.fixtures.anki_builders import (
    NoteSpec,
    NoteTypeSpec,
    build_anki21b_apkg,
    build_legacy_apkg,
    build_text_export,
    legacy_collection_bytes,
    new_collection_bytes,
    zip_entries,
    zstd_compress,
)

VOCAB = NoteTypeSpec(id=1700000000001, name="Japanese Vocab", field_names=("Word", "Reading", "Meaning"))
BASIC = NoteTypeSpec(id=1700000000002, name="Basic", field_names=("Front", "Back"))
EMPTY_TYPE = NoteTypeSpec(id=1700000000003, name="Unused", field_names=("A", "B"))


def _write(tmp_path: Path, data: bytes, name: str = "deck.apkg") -> tuple[Path, Path]:
    path = tmp_path / name
    path.write_bytes(data)
    workdir = tmp_path / "work"
    workdir.mkdir()
    return path, workdir


@pytest.fixture
def opened_entries(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every zip entry name opened for reading through ``ZipFile.open``.

    Writes (the fixture builders use ``ZipFile.writestr``) are not recorded.
    """
    opened: list[str] = []
    original = zipfile.ZipFile.open

    def spy(self, name, mode="r", *args, **kwargs):
        if mode == "r":
            opened.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original(self, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", spy)
    return opened


# ---------------------------------------------------------------- entry names


@pytest.mark.parametrize(
    "name",
    ["/etc/passwd", "C:x", "c:\\x", "a\\b", "..", "../x", "a/../b", "a/..", "/"],
)
def test_unsafe_entry_names(name: str) -> None:
    assert is_unsafe_entry_name(name)
    with pytest.raises(UnsafePackageError):
        check_entry_names(["media", name])


@pytest.mark.parametrize(
    "name", ["0", "media", "meta", "collection.anki2", "a/b.txt", "a..b", "...", "ab:c", "dir/"]
)
def test_safe_entry_names(name: str) -> None:
    assert not is_unsafe_entry_name(name)
    check_entry_names([name])


def test_unsafe_entry_rejects_package_before_any_entry_is_opened(
    tmp_path: Path, opened_entries: list[str]
) -> None:
    data = build_legacy_apkg(
        [BASIC], [NoteSpec(1, BASIC.id, ("a", "b"))], extra_entries=[("a/../evil", b"x")]
    )
    path, workdir = _write(tmp_path, data)
    with pytest.raises(UnsafePackageError):
        read_apkg(path, workdir)
    assert opened_entries == []
    assert list(workdir.iterdir()) == []


# ---------------------------------------------------------------- legacy schema


def test_legacy_collection_reads_note_types_fields_and_notes(tmp_path: Path) -> None:
    notes = [
        NoteSpec(30, VOCAB.id, ("飲む", "のむ", "to drink")),
        NoteSpec(10, VOCAB.id, ("食べる", "たべる", "to eat", "extra")),  # truncated
        NoteSpec(20, BASIC.id, ("front",)),  # padded
        NoteSpec(25, 999, ("orphan", "x")),  # unknown mid: ignored
    ]
    path, workdir = _write(tmp_path, build_legacy_apkg([VOCAB, BASIC, EMPTY_TYPE], notes))

    deck = read_apkg(path, workdir)

    assert deck.kind is FileKind.APKG
    assert {nt.id: (nt.name, nt.field_names, nt.note_count) for nt in deck.note_types} == {
        str(VOCAB.id): ("Japanese Vocab", ("Word", "Reading", "Meaning"), 2),
        str(BASIC.id): ("Basic", ("Front", "Back"), 1),
    }  # field order follows ``ord``; the type with no notes is not listed
    assert [n.values for n in deck.notes(str(VOCAB.id))] == [
        ("食べる", "たべる", "to eat"),
        ("飲む", "のむ", "to drink"),
    ]  # ascending note id
    assert [n.values for n in deck.notes(str(BASIC.id))] == [("front", "")]


def test_legacy_collection_with_bad_models_json_is_unreadable(tmp_path: Path) -> None:
    collection = legacy_collection_bytes([], [NoteSpec(1, 1, ("a",))], models_json="{not json")
    path, workdir = _write(tmp_path, zip_entries([("collection.anki2", collection)]))
    with pytest.raises(UnreadableFileError):
        read_apkg(path, workdir)


def test_collection_with_trigger_and_view_is_read_as_data(tmp_path: Path) -> None:
    extra_sql = [
        "CREATE TABLE log (x TEXT)",
        "CREATE TRIGGER t AFTER INSERT ON notes BEGIN INSERT INTO log VALUES ('fired'); END",
        "CREATE VIEW v AS SELECT flds FROM notes",
    ]
    data = build_legacy_apkg([BASIC], [NoteSpec(1, BASIC.id, ("a", "b"))], extra_sql=extra_sql)
    path, workdir = _write(tmp_path, data)
    deck = read_apkg(path, workdir)
    assert [n.values for n in deck.notes(str(BASIC.id))] == [("a", "b")]


# ---------------------------------------------------------------- new schema (anki21b)


def test_anki21b_is_preferred_over_the_legacy_stub(tmp_path: Path, opened_entries: list[str]) -> None:
    big_id = 2**60 + 7  # beyond JavaScript's safe-integer range
    vocab = NoteTypeSpec(id=big_id, name="語彙", field_names=("単語", "読み", "意味"))
    notes = [NoteSpec(5, big_id, ("猫", "ねこ", "cat")), NoteSpec(2, big_id, ("犬", "いぬ", "dog"))]
    data = build_anki21b_apkg([vocab, EMPTY_TYPE], notes, extra_entries=[("0", b"media bytes")])
    path, workdir = _write(tmp_path, data)

    deck = read_apkg(path, workdir)

    assert [(nt.id, nt.name, nt.field_names, nt.note_count) for nt in deck.note_types] == [
        (str(big_id), "語彙", ("単語", "読み", "意味"), 2)
    ]
    assert [n.values for n in deck.notes(str(big_id))] == [("犬", "いぬ", "dog"), ("猫", "ねこ", "cat")]
    # Only the selected collection is opened; media, meta, and the stub never are.
    assert set(opened_entries) == {"collection.anki21b"}


@pytest.mark.parametrize(
    "present",
    [combo for size in (1, 2, 3) for combo in combinations(COLLECTION_PRIORITY, size)],
)
def test_collection_priority(tmp_path: Path, present: tuple[str, ...]) -> None:
    entries = []
    for name in present:
        note = NoteSpec(1, BASIC.id, (name, "back"))
        if name == "collection.anki21b":
            entries.append((name, zstd_compress(new_collection_bytes([BASIC], [note]))))
        else:
            entries.append((name, legacy_collection_bytes([BASIC], [note])))
    path, workdir = _write(tmp_path, zip_entries(entries))

    deck = read_apkg(path, workdir)

    expected = next(name for name in COLLECTION_PRIORITY if name in present)
    assert deck.notes(str(BASIC.id))[0].values[0] == expected


# ---------------------------------------------------------------- size cap


def test_declared_collection_size_over_200_mib_is_unsafe(tmp_path: Path) -> None:
    path = tmp_path / "big.apkg"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        with zf.open("collection.anki2", "w", force_zip64=True) as out:
            chunk = bytes(1024 * 1024)
            for _ in range(MAX_COLLECTION_BYTES // len(chunk)):
                out.write(chunk)
            out.write(b"\0")
    workdir = tmp_path / "work"
    workdir.mkdir()
    with pytest.raises(UnsafePackageError):
        read_apkg(path, workdir)
    assert list(workdir.iterdir()) == []


def test_zstd_frame_header_over_200_mib_is_unsafe(tmp_path: Path) -> None:
    # Magic, single-segment descriptor with an 8-byte content size, then junk.
    header = b"\x28\xb5\x2f\xfd\xe0" + (MAX_COLLECTION_BYTES + 1).to_bytes(8, "little")
    path, workdir = _write(tmp_path, zip_entries([("collection.anki21b", header + b"junk" * 10)]))
    with pytest.raises(UnsafePackageError):
        read_apkg(path, workdir)
    assert list(workdir.iterdir()) == []


def test_stream_count_aborts_and_deletes_partial_file_when_size_is_not_declared(
    tmp_path: Path,
) -> None:
    notes = [NoteSpec(i, BASIC.id, (f"w{i}", "x" * 200)) for i in range(1, 200)]
    data = build_anki21b_apkg([BASIC], notes, write_content_size=False)
    path, workdir = _write(tmp_path, data)

    assert read_apkg(path, workdir).note_types  # readable without the cap
    (workdir / COLLECTION_FILENAME).unlink()

    with pytest.raises(UnsafePackageError):
        read_apkg(path, workdir, max_collection_bytes=4096)
    assert not (workdir / COLLECTION_FILENAME).exists()


# ---------------------------------------------------------------- unreadable / unrecognized


def test_non_zip_is_unreadable(tmp_path: Path) -> None:
    path, workdir = _write(tmp_path, b"this is not a zip archive")
    with pytest.raises(UnreadableFileError):
        read_apkg(path, workdir)


def test_text_export_renamed_to_apkg_is_unreadable(tmp_path: Path) -> None:
    path, workdir = _write(tmp_path, build_text_export([["a", "b"]]))
    with pytest.raises(UnreadableFileError):
        read_apkg(path, workdir)


def test_non_sqlite_collection_is_unreadable(tmp_path: Path) -> None:
    path, workdir = _write(tmp_path, zip_entries([("collection.anki2", b"x" * 4096)]))
    with pytest.raises(UnreadableFileError):
        read_apkg(path, workdir)


def test_corrupted_entry_crc_is_unreadable(tmp_path: Path) -> None:
    collection = legacy_collection_bytes([BASIC], [NoteSpec(1, BASIC.id, ("a", "b"))])
    data = bytearray(zip_entries([("collection.anki2", collection)], compression=zipfile.ZIP_STORED))
    offset = data.index(b"SQLite format 3") + 200
    data[offset] ^= 0xFF
    path, workdir = _write(tmp_path, bytes(data))
    with pytest.raises(UnreadableFileError):
        read_apkg(path, workdir)
    assert not (workdir / COLLECTION_FILENAME).exists()


def test_zip_without_collection_is_unrecognized(tmp_path: Path) -> None:
    path, workdir = _write(tmp_path, zip_entries([("media", b"{}"), ("0", b"img")]))
    with pytest.raises(UnrecognizedFormatError):
        read_apkg(path, workdir)


def test_sqlite_without_notes_table_is_unrecognized(tmp_path: Path) -> None:
    def other_db() -> bytes:
        db = tmp_path / "other.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE something (x)")
        conn.commit()
        conn.close()
        return db.read_bytes()

    path, workdir = _write(tmp_path, zip_entries([("collection.anki2", other_db())]))
    with pytest.raises(UnrecognizedFormatError):
        read_apkg(path, workdir)


@pytest.mark.parametrize("builder", [build_legacy_apkg, build_anki21b_apkg])
def test_collection_with_zero_notes_has_no_notes(tmp_path: Path, builder) -> None:
    path, workdir = _write(tmp_path, builder([BASIC], []))
    with pytest.raises(NoNotesError):
        read_apkg(path, workdir)


def test_only_unknown_mid_notes_has_no_notes(tmp_path: Path) -> None:
    path, workdir = _write(tmp_path, build_legacy_apkg([BASIC], [NoteSpec(1, 42, ("a", "b"))]))
    with pytest.raises(NoNotesError):
        read_apkg(path, workdir)


# ---------------------------------------------------------------- read-only connection


def test_connection_rejects_writes_and_non_select_statements(tmp_path: Path) -> None:
    db = tmp_path / "c.sqlite"
    db.write_bytes(legacy_collection_bytes([BASIC], [NoteSpec(1, BASIC.id, ("a", "b"))]))
    conn = open_collection_readonly(db)
    try:
        assert conn.execute("SELECT count(*) FROM notes").fetchone() == (1,)
        for statement in (
            "INSERT INTO notes (id) VALUES (2)",
            "DELETE FROM notes",
            "CREATE TABLE t (x)",
            "PRAGMA query_only = OFF",
            f"ATTACH DATABASE '{tmp_path / 'other.db'}' AS other",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(statement)
    finally:
        conn.close()
    assert not (tmp_path / "other.db").exists()


def test_reader_never_enables_extension_loading(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []
    real_connect = sqlite3.connect

    class GuardedConnection(sqlite3.Connection):
        def enable_load_extension(self, enabled):  # pragma: no cover - must not run
            calls.append(enabled)
            return super().enable_load_extension(enabled)

    monkeypatch.setattr(
        apkg_reader.sqlite3,
        "connect",
        lambda *a, **kw: real_connect(*a, factory=GuardedConnection, **kw),
    )
    path, workdir = _write(tmp_path, build_legacy_apkg([BASIC], [NoteSpec(1, BASIC.id, ("a", "b"))]))
    read_apkg(path, workdir)
    assert calls == []
