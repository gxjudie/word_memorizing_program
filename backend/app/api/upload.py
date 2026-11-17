"""Streaming multipart reader for the Anki import upload (Req 2.2, 2.3, 2.40).

Starlette's ``UploadFile`` spools the whole body to disk before the handler
runs, so a size check inside the handler would come too late to bound disk use.
This module instead feeds ``request.stream()`` through python-multipart's
callback ``MultipartParser``. It sees the ``file`` part headers (the file name)
before any body byte, and counts body bytes as they arrive.

``Content-Length`` is never used for a decision; only counted bytes are.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

from app.services.anki_import.errors import (
    FileTooLargeError,
    UnreadableFileError,
    UnsupportedColpkgError,
)
from app.services.anki_import.file_kind import is_colpkg

MAX_UPLOAD_BYTES = 52_428_800  # Req 2.3
MULTIPART_OVERHEAD_BYTES = 1_048_576  # allowance for boundaries and non-file parts

UPLOAD_FILE_NAME = "upload.bin"
FILE_FIELD_NAME = "file"


@dataclass(frozen=True)
class ReceivedFile:
    filename: str  # client file name, basename only
    path: Path  # <workdir>/upload.bin
    size: int


def client_basename(filename: str) -> str:
    """Keep only the last path component, splitting on ``/`` and ``\\``."""
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


def _boundary_of(content_type: str | None) -> bytes:
    if not content_type:
        raise UnreadableFileError()
    ctype, params = parse_options_header(content_type)
    boundary = params.get(b"boundary")
    if ctype.lower() != b"multipart/form-data" or not boundary:
        raise UnreadableFileError()
    return boundary


class _FileCollector:
    """python-multipart callbacks that keep the single ``file`` part."""

    def __init__(self, target: Path) -> None:
        self._target = target
        self._header_field = bytearray()
        self._header_value = bytearray()
        self._headers: dict[bytes, bytes] = {}
        self._in_file_part = False
        self._out: BinaryIO | None = None
        self.filename: str | None = None
        self.size = 0
        self.file_complete = False

    # -- headers --------------------------------------------------------
    def on_part_begin(self) -> None:
        self._headers = {}
        self._header_field.clear()
        self._header_value.clear()
        self._in_file_part = False

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_field += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        self._headers[bytes(self._header_field).strip().lower()] = bytes(self._header_value)
        self._header_field.clear()
        self._header_value.clear()

    def on_headers_finished(self) -> None:
        disposition, params = parse_options_header(self._headers.get(b"content-disposition"))
        if disposition.lower() != b"form-data" or params.get(b"name") != FILE_FIELD_NAME.encode():
            return  # some other part: its bytes are discarded
        if self.filename is not None:
            raise UnreadableFileError()  # more than one file part
        raw_name = params.get(b"filename", b"").decode("utf-8", errors="replace")
        self.filename = client_basename(raw_name)
        # Req 2.2: reject before any body byte of the part is stored.
        if is_colpkg(self.filename):
            raise UnsupportedColpkgError()
        self._out = self._target.open("wb")
        self._in_file_part = True

    # -- body -----------------------------------------------------------
    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if not self._in_file_part or self._out is None:
            return
        length = end - start
        if self.size + length > MAX_UPLOAD_BYTES:
            raise FileTooLargeError()
        self._out.write(data[start:end])
        self.size += length

    def on_part_end(self) -> None:
        if self._in_file_part:
            self.file_complete = True
            self._in_file_part = False
            self.close()

    def close(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None

    def callbacks(self) -> dict:
        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
        }


async def receive_single_file(request: Request, workdir: Path) -> ReceivedFile:
    """Stream the multipart body and store the ``file`` part in ``workdir``.

    - Content-Type must be multipart/form-data with a boundary, else
      ``UnreadableFileError``.
    - A ``.colpkg`` file name raises ``UnsupportedColpkgError`` on the part
      headers, before any body byte is stored (Req 2.2).
    - File-part bytes go to ``workdir/upload.bin``; passing
      ``MAX_UPLOAD_BYTES`` raises ``FileTooLargeError`` at once (Req 2.3).
    - Other parts are discarded, and the whole body is capped at
      ``MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES`` (``FileTooLargeError``).
    - No complete ``file`` part, or a malformed body, raises
      ``UnreadableFileError``.

    The caller owns ``workdir`` and deletes it (with any partial file) on
    every path.
    """
    boundary = _boundary_of(request.headers.get("content-type"))
    target = workdir / UPLOAD_FILE_NAME
    collector = _FileCollector(target)
    parser = MultipartParser(boundary, collector.callbacks())
    body_limit = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
    received = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            received += len(chunk)
            if received > body_limit:
                raise FileTooLargeError()
            parser.write(chunk)
        parser.finalize()
    except FormParserError:
        raise UnreadableFileError() from None
    finally:
        collector.close()

    if collector.filename is None or not collector.file_complete:
        raise UnreadableFileError()
    return ReceivedFile(filename=collector.filename, path=target, size=collector.size)
