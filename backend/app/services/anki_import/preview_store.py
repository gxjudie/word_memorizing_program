"""In-process store of Import_Previews (Req 2.35-2.37, 2.40, 2.42).

A preview record holds the whole parsed deck in memory, so switching note
types and confirming never re-read the uploaded file; the route deletes the
temp directory as soon as parsing ends. Each account has at most one
``PENDING`` preview (a new upload supersedes the old one) and at most one
running import (a per-account ``threading.Lock``).

The store is in-process. A multi-instance deployment needs sticky sessions or
a shared store.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

from app.services.anki_import.errors import (
    ImportInProgressError,
    InvalidNoteTypeError,
    PreviewExpiredError,
)
from app.services.anki_import.mapping import choose_default_note_type
from app.services.anki_import.models import ParsedDeck

PREVIEW_TTL = timedelta(minutes=30)  # Req 2.36
TEMP_PREFIX = "jvt-anki-"


def utc_now() -> datetime:
    return datetime.now(UTC)


class PreviewState(Enum):
    PENDING = "pending"
    IMPORTING = "importing"


@dataclass
class PreviewRecord:
    preview_id: str  # secrets.token_urlsafe(32)
    user_id: str  # owner (Req 2.37)
    file_name: str
    created_at: datetime
    expires_at: datetime  # created_at + TTL
    state: PreviewState
    deck: ParsedDeck
    selected_note_type_id: str | None


class PreviewStore:
    def __init__(
        self,
        clock: Callable[[], datetime] = utc_now,
        ttl: timedelta = PREVIEW_TTL,
        temp_root: Path | None = None,
    ) -> None:
        self._clock = clock
        self._ttl = ttl
        self._temp_root = temp_root  # None -> the system temp directory
        self._lock = threading.Lock()
        self._records: dict[str, PreviewRecord] = {}
        self._import_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------ temp dirs
    def new_workdir(self) -> Path:
        """A fresh directory dedicated to one upload (Req 2.40)."""
        root = str(self._temp_root) if self._temp_root is not None else None
        return Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=root))

    @staticmethod
    def discard_workdir(path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)

    def remove_stale_workdirs(self) -> None:
        """Delete ``jvt-anki-*`` directories older than the TTL (left by a crash)."""
        root = self._temp_root if self._temp_root is not None else Path(tempfile.gettempdir())
        cutoff = (self._clock() - self._ttl).timestamp()
        try:
            entries = list(os.scandir(root))
        except OSError:
            return
        for entry in entries:
            try:
                if not entry.name.startswith(TEMP_PREFIX):
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            shutil.rmtree(entry.path, ignore_errors=True)

    # ------------------------------------------------------------- records
    def sweep(self) -> None:
        """Drop expired ``PENDING`` records."""
        with self._lock:
            self._sweep_locked()

    def _sweep_locked(self) -> None:
        now = self._clock()
        expired = [
            pid
            for pid, rec in self._records.items()
            if rec.state is PreviewState.PENDING and now >= rec.expires_at
        ]
        for pid in expired:
            del self._records[pid]

    def _pending_locked(self, preview_id: str, user_id: str) -> PreviewRecord:
        """Unknown, foreign, expired, and non-pending previews look the same."""
        record = self._records.get(preview_id)
        if (
            record is None
            or record.user_id != user_id
            or record.state is not PreviewState.PENDING
            or self._clock() >= record.expires_at
        ):
            raise PreviewExpiredError()
        return record

    def add(self, user_id: str, file_name: str, deck: ParsedDeck) -> PreviewRecord:
        """Store a new preview and supersede the account's earlier pending ones."""
        with self._lock:
            self._sweep_locked()
            superseded = [
                pid
                for pid, rec in self._records.items()
                if rec.user_id == user_id and rec.state is PreviewState.PENDING
            ]
            for pid in superseded:
                del self._records[pid]
            now = self._clock()
            selected = (
                choose_default_note_type(deck.note_types).id if deck.note_types else None
            )
            record = PreviewRecord(
                preview_id=secrets.token_urlsafe(32),
                user_id=user_id,
                file_name=file_name,
                created_at=now,
                expires_at=now + self._ttl,
                state=PreviewState.PENDING,
                deck=deck,
                selected_note_type_id=selected,
            )
            self._records[record.preview_id] = record
            return record

    def get_pending(self, preview_id: str, user_id: str) -> PreviewRecord:
        with self._lock:
            self._sweep_locked()
            return self._pending_locked(preview_id, user_id)

    def select_note_type(
        self, preview_id: str, user_id: str, note_type_id: str
    ) -> PreviewRecord:
        with self._lock:
            self._sweep_locked()
            record = self._pending_locked(preview_id, user_id)
            if not any(nt.id == note_type_id for nt in record.deck.note_types):
                raise InvalidNoteTypeError()
            record.selected_note_type_id = note_type_id
            return record

    @contextmanager
    def begin_import(self, preview_id: str, user_id: str) -> Iterator[PreviewRecord]:
        """Run one import for the account (Req 2.42).

        A held account lock raises ``ImportInProgressError`` and leaves the
        record ``PENDING``. Otherwise the record becomes ``IMPORTING`` and is
        removed when the body finishes, whether it succeeds or raises.
        """
        with self._lock:
            self._sweep_locked()
            account_lock = self._import_locks.setdefault(user_id, threading.Lock())
        if not account_lock.acquire(blocking=False):
            raise ImportInProgressError()
        try:
            with self._lock:
                self._sweep_locked()
                record = self._pending_locked(preview_id, user_id)
                record.state = PreviewState.IMPORTING
            try:
                yield record
            finally:
                with self._lock:
                    self._records.pop(preview_id, None)
        finally:
            account_lock.release()

    def cancel(self, preview_id: str, user_id: str) -> None:
        """Owner-only and idempotent; never touches an ``IMPORTING`` record."""
        with self._lock:
            self._sweep_locked()
            record = self._records.get(preview_id)
            if (
                record is not None
                and record.user_id == user_id
                and record.state is PreviewState.PENDING
            ):
                del self._records[preview_id]
