"""Unit tests for the streaming upload reader and the preview store (task 17.13)."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from starlette.requests import Request

from app.api import upload
from app.api.upload import MAX_UPLOAD_BYTES, receive_single_file
from app.services.anki_import.errors import (
    FileTooLargeError,
    ImportInProgressError,
    InvalidNoteTypeError,
    PreviewExpiredError,
    UnreadableFileError,
    UnsupportedColpkgError,
)
from app.services.anki_import.models import AnkiNote, FileKind, NoteType, ParsedDeck
from app.services.anki_import.preview_store import (
    TEMP_PREFIX,
    PreviewState,
    PreviewStore,
)

BOUNDARY = "testboundary123"


# ------------------------------------------------------------ upload helpers
def _part(name: str, body: bytes, filename: str | None = None) -> bytes:
    disposition = f'form-data; name="{name}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    return (
        f"--{BOUNDARY}\r\nContent-Disposition: {disposition}\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + body + b"\r\n"


def _body(*parts: bytes) -> bytes:
    return b"".join(parts) + f"--{BOUNDARY}--\r\n".encode()


class _Stream:
    """An ASGI receive callable that yields chunks and counts what was pulled."""

    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.pulled = 0

    async def __call__(self):
        try:
            chunk = next(self._chunks)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        self.pulled += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}


def _request(stream, content_type: str | None = f"multipart/form-data; boundary={BOUNDARY}"):
    headers = [] if content_type is None else [(b"content-type", content_type.encode())]
    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    return Request(scope, stream)


def _chunked(data: bytes, size: int = 65_536):
    for i in range(0, len(data), size):
        yield data[i : i + size]


def _receive(stream, workdir: Path, **kw):
    return asyncio.run(receive_single_file(_request(stream, **kw), workdir))


# ------------------------------------------------------------ upload reader
def test_receives_file_part_and_keeps_basename(tmp_path):
    body = _body(
        _part("other", b"ignored"),
        _part("file", "単語\tword\n".encode(), filename="C:\\Users\\x/dir/deck.txt"),
    )
    received = _receive(_Stream(_chunked(body, 7)), tmp_path)
    assert received.filename == "deck.txt"
    assert received.path == tmp_path / "upload.bin"
    assert received.path.read_bytes() == "単語\tword\n".encode()
    assert received.size == len("単語\tword\n".encode())


def test_colpkg_rejected_before_body_is_stored(tmp_path):
    def endless_body():
        yield (
            f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; "
            'filename="deck.COLPKG"\r\n\r\n'
        ).encode()
        while True:
            yield b"x" * 65_536

    stream = _Stream(endless_body())
    with pytest.raises(UnsupportedColpkgError):
        _receive(stream, tmp_path)
    assert stream.pulled < 1_048_576
    upload_bin = tmp_path / "upload.bin"
    assert not upload_bin.exists() or upload_bin.stat().st_size == 0


def test_file_over_limit_raises_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 1000)
    stream = _Stream(_chunked(_body(_part("file", b"a" * 5000, filename="deck.apkg")), 512))
    with pytest.raises(FileTooLargeError):
        _receive(stream, tmp_path)
    assert (tmp_path / "upload.bin").stat().st_size <= 1000


def test_file_at_exact_limit_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 1000)
    body = _body(_part("file", b"a" * 1000, filename="deck.apkg"))
    assert _receive(_Stream(_chunked(body, 300)), tmp_path).size == 1000


def test_total_body_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(upload, "MULTIPART_OVERHEAD_BYTES", 1000)
    body = _body(_part("junk", b"j" * 5000), _part("file", b"a", filename="deck.apkg"))
    with pytest.raises(FileTooLargeError):
        _receive(_Stream(_chunked(body, 512)), tmp_path)


def test_real_limit_constant():
    assert MAX_UPLOAD_BYTES == 52_428_800


def test_missing_file_part_is_unreadable(tmp_path):
    with pytest.raises(UnreadableFileError):
        _receive(_Stream([_body(_part("other", b"x"))]), tmp_path)


@pytest.mark.parametrize(
    "content_type",
    [None, "application/json", "multipart/form-data", "text/plain; boundary=x"],
)
def test_non_multipart_content_type_is_unreadable(tmp_path, content_type):
    body = _body(_part("file", b"x", filename="deck.txt"))
    with pytest.raises(UnreadableFileError):
        _receive(_Stream([body]), tmp_path, content_type=content_type)


def test_truncated_body_is_unreadable(tmp_path):
    body = _body(_part("file", b"abcdef", filename="deck.txt"))
    with pytest.raises(UnreadableFileError):
        _receive(_Stream([body[: body.index(b"abc") + 3]]), tmp_path)


# ------------------------------------------------------------ preview store
class FakeClock:
    def __init__(self):
        self.now = datetime(2025, 1, 1, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def _apkg_deck() -> ParsedDeck:
    note = AnkiNote(values=("猫", "ねこ", "cat"))
    return ParsedDeck(
        kind=FileKind.APKG,
        note_types=(
            NoteType(id="1", name="Basic", field_names=("Front", "Back"), note_count=1),
            NoteType(id="2", name="Vocab", field_names=("Word", "Reading", "Meaning"), note_count=2),
        ),
        text_field_names=(),
        notes_by_type={"1": (note,), "2": (note, note)},
    )


def _text_deck() -> ParsedDeck:
    return ParsedDeck(
        kind=FileKind.TEXT,
        note_types=(),
        text_field_names=("열 1", "열 2"),
        notes_by_type={None: (AnkiNote(values=("a", "b")),)},
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(clock, tmp_path):
    return PreviewStore(clock=clock, temp_root=tmp_path)


def test_add_sets_ids_expiry_and_default_note_type(store, clock):
    record = store.add("u1", "deck.apkg", _apkg_deck())
    assert len(record.preview_id) >= 40
    assert record.state is PreviewState.PENDING
    assert record.expires_at == clock.now + timedelta(minutes=30)
    assert record.selected_note_type_id == "2"
    assert store.add("u2", "deck.txt", _text_deck()).selected_note_type_id is None


def test_get_pending_expires_at_thirty_minutes(store, clock):
    record = store.add("u1", "deck.apkg", _apkg_deck())
    clock.advance(minutes=29, seconds=59)
    assert store.get_pending(record.preview_id, "u1") is record
    clock.advance(seconds=1)
    with pytest.raises(PreviewExpiredError):
        store.get_pending(record.preview_id, "u1")


def test_unknown_and_foreign_previews_look_expired(store):
    record = store.add("u1", "deck.apkg", _apkg_deck())
    with pytest.raises(PreviewExpiredError):
        store.get_pending("nope", "u1")
    with pytest.raises(PreviewExpiredError):
        store.get_pending(record.preview_id, "u2")


def test_new_upload_supersedes_pending_preview(store):
    first = store.add("u1", "a.apkg", _apkg_deck())
    other = store.add("u2", "b.apkg", _apkg_deck())
    second = store.add("u1", "c.apkg", _apkg_deck())
    with pytest.raises(PreviewExpiredError):
        store.get_pending(first.preview_id, "u1")
    assert store.get_pending(second.preview_id, "u1") is second
    assert store.get_pending(other.preview_id, "u2") is other


def test_select_note_type(store):
    record = store.add("u1", "deck.apkg", _apkg_deck())
    assert store.select_note_type(record.preview_id, "u1", "1").selected_note_type_id == "1"
    with pytest.raises(InvalidNoteTypeError):
        store.select_note_type(record.preview_id, "u1", "999")
    with pytest.raises(PreviewExpiredError):
        store.select_note_type(record.preview_id, "u2", "1")


def test_confirm_twice_and_after_failure_is_expired(store):
    record = store.add("u1", "deck.apkg", _apkg_deck())
    with store.begin_import(record.preview_id, "u1") as rec:
        assert rec.state is PreviewState.IMPORTING
    with pytest.raises(PreviewExpiredError):
        store.get_pending(record.preview_id, "u1")
    with pytest.raises(PreviewExpiredError):
        with store.begin_import(record.preview_id, "u1"):
            pass

    failing = store.add("u1", "deck.apkg", _apkg_deck())
    with pytest.raises(RuntimeError):
        with store.begin_import(failing.preview_id, "u1"):
            raise RuntimeError("boom")
    with pytest.raises(PreviewExpiredError):
        store.get_pending(failing.preview_id, "u1")


def test_confirm_after_cancel_and_foreign_confirm(store):
    record = store.add("u1", "deck.apkg", _apkg_deck())
    with pytest.raises(PreviewExpiredError):
        with store.begin_import(record.preview_id, "u2"):
            pass
    store.cancel(record.preview_id, "u2")  # foreign cancel does nothing
    assert store.get_pending(record.preview_id, "u1") is record
    store.cancel(record.preview_id, "u1")
    store.cancel(record.preview_id, "u1")  # idempotent
    with pytest.raises(PreviewExpiredError):
        with store.begin_import(record.preview_id, "u1"):
            pass


def test_import_in_progress_keeps_preview_pending(store):
    running = store.add("u1", "a.apkg", _apkg_deck())
    started, release = threading.Event(), threading.Event()

    def worker():
        with store.begin_import(running.preview_id, "u1"):
            started.set()
            release.wait(5)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert started.wait(5)
        waiting = store.add("u1", "b.apkg", _apkg_deck())  # IMPORTING not superseded
        with pytest.raises(ImportInProgressError):
            with store.begin_import(waiting.preview_id, "u1"):
                pass
        assert store.get_pending(waiting.preview_id, "u1").state is PreviewState.PENDING
        store.cancel(running.preview_id, "u1")  # cannot cancel an IMPORTING record
    finally:
        release.set()
        thread.join(5)
    with store.begin_import(waiting.preview_id, "u1"):
        pass


def test_sweep_drops_expired_pending(store, clock):
    record = store.add("u1", "deck.apkg", _apkg_deck())
    clock.advance(minutes=31)
    store.sweep()
    assert record.preview_id not in store._records


def test_workdirs_and_stale_cleanup(store, clock, tmp_path):
    workdir = store.new_workdir()
    assert workdir.parent == tmp_path and workdir.name.startswith(TEMP_PREFIX)
    (workdir / "upload.bin").write_bytes(b"x")

    old = tmp_path / f"{TEMP_PREFIX}old"
    old.mkdir()
    unrelated = tmp_path / "other-old"
    unrelated.mkdir()
    old_time = clock.now.timestamp() - 3600
    os.utime(old, (old_time, old_time))
    os.utime(unrelated, (old_time, old_time))
    os.utime(workdir, (clock.now.timestamp(), clock.now.timestamp()))

    store.remove_stale_workdirs()
    assert not old.exists()
    assert unrelated.exists()
    assert workdir.exists()

    PreviewStore.discard_workdir(workdir)
    assert not workdir.exists()
    PreviewStore.discard_workdir(workdir)  # missing directory is fine


def test_default_clock_is_utc():
    store = PreviewStore()
    record = store.add("u1", "deck.txt", _text_deck())
    assert record.created_at.tzinfo is not None
    assert abs(record.created_at.timestamp() - time.time()) < 5
