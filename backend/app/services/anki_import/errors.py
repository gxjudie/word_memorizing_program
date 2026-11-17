"""Coded errors raised by the Anki import.

Every error carries a stable ``code`` and a fixed English ``message``. The
messages are constants: they never echo file content, zip entry names, or
SQLite error text, so a crafted file cannot reflect data into a response.
The route layer maps each ``code`` to an HTTP status; the SPA shows its own
Korean text per ``code``.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


class AnkiImportError(Exception):
    """Base class for every Anki import failure."""

    code: str = "anki_import_error"
    message: str = "The Anki import failed."

    def __init__(self, code: str | None = None, message: str | None = None) -> None:
        # Subclasses use their class-level code and fixed message.
        if code is not None:
            self.code = code
        if message is not None:
            self.message = message
        super().__init__(self.message)


class UnsupportedColpkgError(AnkiImportError):
    """Req 2.2: a ``.colpkg`` upload, rejected regardless of size or content."""

    code = "unsupported_colpkg"
    message = (
        "Anki collection packages (.colpkg) are not supported. Export the deck "
        "from Anki as a .apkg deck package or as notes in plain text."
    )


class FileTooLargeError(AnkiImportError):
    """Req 2.3: more than 52,428,800 bytes uploaded."""

    code = "file_too_large"
    message = "The file is larger than 50 MB (52,428,800 bytes)."


class UnsupportedExtensionError(AnkiImportError):
    """Req 2.1, 2.3: an extension other than .apkg, .txt, or .tsv."""

    code = "unsupported_extension"
    message = "Only .apkg, .txt, and .tsv files can be imported."


class UnreadableFileError(AnkiImportError):
    """Req 2.3: the file is unreadable or corrupted."""

    code = "unreadable_file"
    message = "The file could not be read. It may be corrupted."


class UnsafePackageError(AnkiImportError):
    """Req 2.41: an unsafe entry name or an oversized collection file."""

    code = "unsafe_package"
    message = "The package contains an unsafe entry or a collection file that is too large."


class InvalidTextExportError(AnkiImportError):
    """Req 2.11: not a UTF-8 tab-separated Anki plain-text export."""

    code = "invalid_text_export"
    message = "Only UTF-8 tab-separated Anki plain-text exports are supported."


class UnrecognizedFormatError(AnkiImportError):
    """Req 2.3: not a recognized Anki_Deck_File format."""

    code = "unrecognized_format"
    message = "The file is not a recognized Anki deck format."


class NoNotesError(AnkiImportError):
    """Req 2.3: the file contains no Anki_Note."""

    code = "no_notes"
    message = "The file contains no notes."


class InvalidNoteTypeError(AnkiImportError):
    """Req 2.9: a note type id that is not among the listed note types."""

    code = "invalid_note_type"
    message = "The selected note type is not in this deck."


class InvalidMappingError(AnkiImportError):
    """Req 2.22: the Field_Mapping is invalid for one or more roles.

    ``errors`` maps each invalid role name (``"word"``, ``"reading"``,
    ``"meaning"``) to its reason (``"missing"``, ``"duplicate"``, or
    ``"out_of_range"``).
    """

    code = "invalid_mapping"
    message = "The field mapping is invalid."

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__()
        # Role is a str enum, so both Role keys and plain strings are accepted.
        self.errors: Mapping[str, str] = MappingProxyType(
            {str(getattr(role, "value", role)): reason for role, reason in errors.items()}
        )


class PreviewExpiredError(AnkiImportError):
    """Req 2.36, 2.37: unknown, expired, used, cancelled, or foreign preview."""

    code = "preview_expired"
    message = "The import preview is no longer available. Upload the file again."


class ImportInProgressError(AnkiImportError):
    """Req 2.42: another import for the same account is running."""

    code = "import_in_progress"
    message = "Another import is in progress."


class ImportFailedError(AnkiImportError):
    """Req 2.31: the import stopped and was rolled back."""

    code = "import_failed"
    message = "The import failed. No words were imported."
