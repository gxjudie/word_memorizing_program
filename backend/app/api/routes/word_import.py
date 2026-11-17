"""Anki deck import routes (Requirement 2).

Four routes under ``/api/words/import``, all depending on ``CurrentUser``:

* ``POST /preview`` — stream one multipart ``file`` into a fresh temp
  directory, parse it, and return ``201 ImportPreviewOut``. The handler
  declares no body parameter, so the App_Session is checked before any body
  byte is read (Req 2.38). The temp directory is deleted on every path
  (Req 2.40).
* ``GET /preview/{preview_id}?note_type_id=`` — the preview, optionally
  switched to another note type (Req 2.9).
* ``POST /preview/{preview_id}/confirm`` — run the import. The body is parsed
  here, so a malformed body becomes ``422 invalid_mapping`` with per-role
  errors instead of FastAPI's list-shaped ``422``.
* ``DELETE /preview/{preview_id}`` — cancel; ``204``, idempotent, owner-only.

Every ``AnkiImportError`` goes through :func:`to_http_exception`, which sends
``{"detail": {"code", "message"}}`` (plus ``errors`` for ``invalid_mapping``)
with the status from the design's error table.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, get_anki_import_service, get_preview_store
from app.api.upload import receive_single_file
from app.schemas.word_import import (
    ConfirmImportIn,
    FieldMappingOut,
    ImportPreviewOut,
    ImportSummaryOut,
)
from app.services.anki_import.errors import (
    AnkiImportError,
    FileTooLargeError,
    ImportFailedError,
    ImportInProgressError,
    InvalidMappingError,
    InvalidNoteTypeError,
    InvalidTextExportError,
    NoNotesError,
    PreviewExpiredError,
    UnreadableFileError,
    UnrecognizedFormatError,
    UnsafePackageError,
    UnsupportedColpkgError,
    UnsupportedExtensionError,
)
from app.services.anki_import.import_service import AnkiImportService
from app.services.anki_import.preview_store import PreviewStore

logger = logging.getLogger(__name__)

router = APIRouter()

ImportServiceDep = Annotated[AnkiImportService, Depends(get_anki_import_service)]
PreviewStoreDep = Annotated[PreviewStore, Depends(get_preview_store)]

# ---- error translation ---------------------------------------------------------

# 413 and 422 are literals: Starlette renamed their constants across versions.
ERROR_STATUS: dict[type[AnkiImportError], int] = {
    UnsupportedColpkgError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FileTooLargeError: 413,
    UnsupportedExtensionError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    UnreadableFileError: status.HTTP_400_BAD_REQUEST,
    UnsafePackageError: status.HTTP_400_BAD_REQUEST,
    InvalidTextExportError: status.HTTP_400_BAD_REQUEST,
    UnrecognizedFormatError: status.HTTP_400_BAD_REQUEST,
    NoNotesError: status.HTTP_400_BAD_REQUEST,
    InvalidNoteTypeError: status.HTTP_400_BAD_REQUEST,
    InvalidMappingError: 422,
    PreviewExpiredError: status.HTTP_410_GONE,
    ImportInProgressError: status.HTTP_409_CONFLICT,
    ImportFailedError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

# Fixed English per-role messages; the SPA shows its own Korean text.
MAPPING_REASON_MESSAGES: dict[str, str] = {
    "missing": "Choose a field for this role.",
    "duplicate": "This field is already used by another role.",
    "out_of_range": "The selected field does not exist.",
}


def _status_for(exc: AnkiImportError) -> int:
    for cls in type(exc).__mro__:
        if cls in ERROR_STATUS:
            return ERROR_STATUS[cls]  # type: ignore[index]
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def to_http_exception(exc: AnkiImportError) -> HTTPException:
    """Map an import error to ``{"detail": {"code", "message"[, "errors"]}}``."""
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if isinstance(exc, InvalidMappingError):
        detail["errors"] = [
            {
                "field": role,
                "reason": reason,
                "message": MAPPING_REASON_MESSAGES.get(reason, "Invalid field."),
            }
            for role, reason in exc.errors.items()
        ]
    return HTTPException(status_code=_status_for(exc), detail=detail)


# ---- confirm body parsing --------------------------------------------------------

# A non-string note type id can never match a listed id (ids are digit strings),
# so it is replaced by a value that fails the service's note type check.
_UNMATCHABLE_NOTE_TYPE_ID = "invalid-note-type-id"


def _role_value(raw: Any, *, reading: bool) -> int | None:
    """An integer index, ``None``, or an invalid sentinel for a malformed value.

    ``validate_mapping`` reports a malformed word or meaning as ``missing``
    (sentinel ``None``) and a malformed reading as ``out_of_range`` (sentinel
    ``-1``), so every malformed role is named in the per-role errors.
    """
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return -1 if reading else None


def parse_confirm_body(raw: bytes) -> ConfirmImportIn:
    """Parse the confirm body without ever failing on its shape.

    Invalid JSON, a non-object body, a missing or non-object ``mapping``, and
    non-integer role values all become role values that the service's
    ``validate_mapping`` rejects as ``invalid_mapping``, after its
    ``preview_expired`` and ``invalid_note_type`` checks.
    """
    try:
        data = json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict):
        data = {}

    raw_note_type = data.get("note_type_id")
    if raw_note_type is None or isinstance(raw_note_type, str):
        note_type_id = raw_note_type
    elif isinstance(raw_note_type, int) and not isinstance(raw_note_type, bool):
        note_type_id = str(raw_note_type)
    else:
        note_type_id = _UNMATCHABLE_NOTE_TYPE_ID

    mapping = data.get("mapping")
    if not isinstance(mapping, dict):
        mapping = {}
    return ConfirmImportIn(
        note_type_id=note_type_id,
        mapping=FieldMappingOut(
            word=_role_value(mapping.get("word"), reading=False),
            reading=_role_value(mapping.get("reading"), reading=True),
            meaning=_role_value(mapping.get("meaning"), reading=False),
        ),
    )


# ---- routes ----------------------------------------------------------------------


@router.post(
    "/preview",
    response_model=ImportPreviewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an Anki deck and create an Import_Preview",
)
async def create_import_preview(
    request: Request,
    user: CurrentUser,
    service: ImportServiceDep,
    store: PreviewStoreDep,
) -> ImportPreviewOut:
    """Stream the ``file`` part, parse it, and store a 30-minute preview (Req 2.1-2.15).

    No Gemini key is required. Nothing is written to the Word_Collection.
    """
    workdir = store.new_workdir()
    try:
        received = await receive_single_file(request, workdir)
        return await run_in_threadpool(service.create_preview, user.id, received, workdir)
    except AnkiImportError as exc:
        raise to_http_exception(exc) from None
    finally:
        store.discard_workdir(workdir)


@router.get(
    "/preview/{preview_id}",
    response_model=ImportPreviewOut,
    summary="Get an Import_Preview, optionally switching the note type",
)
def get_import_preview(
    preview_id: str,
    user: CurrentUser,
    service: ImportServiceDep,
    note_type_id: Annotated[str | None, Query()] = None,
) -> ImportPreviewOut:
    """Return the pending preview; ``note_type_id`` switches the `.apkg` note type (Req 2.9)."""
    try:
        return service.get_preview(user.id, preview_id, note_type_id)
    except AnkiImportError as exc:
        raise to_http_exception(exc) from None


@router.post(
    "/preview/{preview_id}/confirm",
    response_model=ImportSummaryOut,
    summary="Confirm an Import_Preview and run the import",
)
async def confirm_import(
    preview_id: str,
    request: Request,
    user: CurrentUser,
    service: ImportServiceDep,
) -> ImportSummaryOut:
    """Run the import in one transaction and return the Import_Summary (Req 2.21-2.34)."""
    body = parse_confirm_body(await request.body())
    try:
        return await run_in_threadpool(service.confirm, user.id, preview_id, body)
    except ImportFailedError as exc:
        # Preview id and exception type only: never values, rows, or SQL text.
        cause = exc.__context__ if exc.__context__ is not None else exc
        logger.warning(
            "Anki import failed: preview_id=%s error=%s", preview_id, type(cause).__name__
        )
        raise to_http_exception(exc) from None
    except AnkiImportError as exc:
        raise to_http_exception(exc) from None


@router.delete(
    "/preview/{preview_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Cancel an Import_Preview",
)
def cancel_import_preview(
    preview_id: str,
    user: CurrentUser,
    service: ImportServiceDep,
) -> Response:
    """Discard the preview; idempotent and owner-only (Req 2.35)."""
    service.cancel(user.id, preview_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
