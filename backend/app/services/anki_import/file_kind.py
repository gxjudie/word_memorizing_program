"""PURE extension rules for uploaded files (Req 2.1, 2.2, 2.3)."""
from __future__ import annotations

from app.services.anki_import.errors import UnsupportedExtensionError
from app.services.anki_import.models import FileKind

_TEXT_EXTENSIONS = frozenset({".txt", ".tsv"})


def _basename(filename: str) -> str:
    """The last path component, splitting on both ``/`` and ``\\``."""
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


def extension_of(filename: str) -> str:
    """The lowercased suffix from the last ``.`` of the basename, or ``""``."""
    base = _basename(filename)
    dot = base.rfind(".")
    return base[dot:].lower() if dot >= 0 else ""


def is_colpkg(filename: str) -> bool:
    """True for a ``.colpkg`` file name, compared case-insensitively (Req 2.2)."""
    return extension_of(filename) == ".colpkg"


def file_kind_for(filename: str) -> FileKind:
    """``.apkg`` -> APKG; ``.txt``/``.tsv`` -> TEXT; else ``UnsupportedExtensionError``."""
    ext = extension_of(filename)
    if ext == ".apkg":
        return FileKind.APKG
    if ext in _TEXT_EXTENSIONS:
        return FileKind.TEXT
    raise UnsupportedExtensionError()
