"""Secret handling for Learner-supplied Gemini API keys (BYOK).

- :class:`SecretCipher` is the Fernet cipher keyed by ``TOKEN_ENCRYPTION_KEY``,
  the same key that encrypts the auth-start cookie. It encrypts Gemini keys at
  rest (Req 10.11).
- :func:`normalize_gemini_key` trims and format-checks a submitted key
  (Req 10.5, 10.6). The check is **format-agnostic**: after trimming it rejects
  only an empty value, any internal whitespace, any non-printable/control
  character, or a length outside 20-200. It asserts no prefix and no fixed
  length, so both the older ``AIza…`` shape and the newer ``AQ.…`` shape (and
  future shapes) pass to the verification request, which decides real validity.
  It is pure and never touches the network.
- :func:`key_hint` returns the last 4 characters, the only browser-visible
  form of a stored key (Req 10.12).
- :func:`redact` removes a secret from any text before it is logged
  (Req 10.13).

No exception raised here contains a key or a ciphertext.
"""
from __future__ import annotations

from typing import ClassVar

from cryptography.fernet import Fernet, InvalidToken

__all__ = [
    "KEY_MAX_LENGTH",
    "KEY_MIN_LENGTH",
    "REDACTED",
    "KeyFormatError",
    "SecretCipher",
    "SecretCipherConfigError",
    "SecretDecryptionError",
    "key_hint",
    "normalize_gemini_key",
    "redact",
]

# Format-agnostic length bounds for a trimmed key (Req 10.6). No prefix or
# fixed-length shape is assumed, so both "AIza…" and "AQ.…" keys are accepted.
KEY_MIN_LENGTH = 20
KEY_MAX_LENGTH = 200

REDACTED = "[REDACTED]"


class _FixedMessageError(Exception):
    """Exception whose message is a fixed, secret-free string by default."""

    default_message: ClassVar[str] = "Secret handling failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.default_message)


class KeyFormatError(_FixedMessageError, ValueError):
    """The trimmed key is empty, has internal whitespace or a non-printable or
    control character, or has a length outside 20-200.

    The check is format-agnostic: no prefix and no fixed length is required, so
    any key shape Google issues (``AIza…``, ``AQ.…``, or a future shape) passes
    and its real validity is decided by the verification request.

    Req 10.6 -> ``400 invalid_key_format``. Nothing is stored and no Gemini
    request is sent.
    """

    default_message: ClassVar[str] = "The Gemini API key format is invalid."


class SecretCipherConfigError(_FixedMessageError):
    """``TOKEN_ENCRYPTION_KEY`` is missing or not a valid Fernet key."""

    default_message: ClassVar[str] = "TOKEN_ENCRYPTION_KEY is missing or invalid."


class SecretDecryptionError(_FixedMessageError):
    """A stored ciphertext could not be decrypted with the current key."""

    default_message: ClassVar[str] = "A stored secret could not be decrypted."


class SecretCipher:
    """Fernet encryption of short text secrets keyed by ``TOKEN_ENCRYPTION_KEY``.

    The ciphertext is a Fernet token (URL-safe base64 text), which never equals
    and never contains the plaintext.
    """

    def __init__(self, key: str | bytes) -> None:
        """Build the cipher; raise :class:`SecretCipherConfigError` on a bad key."""
        if not key:
            raise SecretCipherConfigError()
        try:
            raw = key.encode("ascii") if isinstance(key, str) else key
            self._fernet = Fernet(raw)
        except (ValueError, TypeError, UnicodeEncodeError):
            raise SecretCipherConfigError() from None

    @property
    def fernet(self) -> Fernet:
        """The underlying Fernet instance (shared with the auth-start cookie)."""
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        """Return the Fernet ciphertext of ``plaintext`` as ASCII text."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Return the plaintext; raise :class:`SecretDecryptionError` on failure."""
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError, UnicodeError):
            raise SecretDecryptionError() from None

    def __repr__(self) -> str:
        """Never expose key material."""
        return "SecretCipher(<redacted>)"


def normalize_gemini_key(raw: str) -> str:
    """Trim leading/trailing whitespace, then format-check the key.

    Returns the trimmed key (Req 10.5). Raises :class:`KeyFormatError` when the
    trimmed value is empty, contains any internal whitespace character, contains
    any non-printable or control character, or has a length below
    :data:`KEY_MIN_LENGTH` or above :data:`KEY_MAX_LENGTH` (Req 10.6).

    The rule is format-agnostic: it asserts no prefix and no fixed length, so
    both the older ``AIza…`` shape and the newer ``AQ.…`` shape pass. Real
    validity is decided later by the Gemini verification request.
    """
    if not isinstance(raw, str):
        raise KeyFormatError()
    key = raw.strip()
    if not KEY_MIN_LENGTH <= len(key) <= KEY_MAX_LENGTH:
        raise KeyFormatError()
    if any(not ch.isprintable() or ch.isspace() for ch in key):
        raise KeyFormatError()
    return key


def key_hint(key: str) -> str:
    """Return the last 4 characters of ``key`` (Req 10.11, 10.12)."""
    return key[-4:]


def redact(text: str, secret: str) -> str:
    """Replace every occurrence of ``secret`` in ``text`` with ``[REDACTED]``."""
    if not secret:
        return text
    return text.replace(secret, REDACTED)
