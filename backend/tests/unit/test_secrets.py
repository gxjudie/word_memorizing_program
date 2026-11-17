"""Unit tests for Gemini key helpers in ``app.auth.secrets`` (task 8.5)."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.auth.secrets import (
    KEY_MAX_LENGTH,
    KEY_MIN_LENGTH,
    KeyFormatError,
    SecretCipher,
    SecretCipherConfigError,
    SecretDecryptionError,
    key_hint,
    normalize_gemini_key,
    redact,
)

# Old-style AIza key (39 chars) and new-style AQ. key (43 chars). The rule is
# format-agnostic, so both must be accepted (Req 10.6).
KEY = "AIza" + "Ab3_-" * 7  # 4 + 35 = 39 chars
KEY_AQ = "AQ." + "Xy9_-Zt1" * 5  # 3 + 40 = 43 chars


@pytest.mark.parametrize("key", [KEY, KEY_AQ])
def test_normalize_trims_and_accepts_well_formed_key(key: str) -> None:
    assert normalize_gemini_key(f"  \t{key}\n ") == key


def test_normalize_accepts_boundary_lengths() -> None:
    shortest = "k" * KEY_MIN_LENGTH
    longest = "k" * KEY_MAX_LENGTH
    assert normalize_gemini_key(shortest) == shortest
    assert normalize_gemini_key(longest) == longest


@pytest.mark.parametrize(
    "raw",
    [
        "",  # empty
        "   \n\t",  # whitespace only -> empty after strip
        "k" * (KEY_MIN_LENGTH - 1),  # too short (< 20)
        "k" * (KEY_MAX_LENGTH + 1),  # too long (> 200)
        KEY[:20] + " " + KEY[21:],  # internal whitespace
        KEY[:20] + "\t" + KEY[21:],  # internal tab
        KEY[:-1] + "\x00",  # control character
        KEY[:-1] + "\x7f",  # DEL control character
        KEY[:-1] + "\x9f",  # C1 control (not stripped, not printable)
        KEY[:20] + "\u0085" + KEY[21:],  # C1 next-line in the interior (whitespace)
    ],
)
def test_normalize_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(KeyFormatError) as exc_info:
        normalize_gemini_key(raw)
    assert raw.strip() == "" or raw.strip() not in str(exc_info.value)


def test_key_hint_is_last_four_characters() -> None:
    assert key_hint(KEY) == KEY[-4:]


def test_redact_replaces_every_occurrence() -> None:
    text = f"bad key {KEY}; again {KEY}"
    assert redact(text, KEY) == "bad key [REDACTED]; again [REDACTED]"
    assert redact("nothing here", KEY) == "nothing here"
    assert redact("text", "") == "text"


def test_cipher_round_trip_never_contains_plaintext() -> None:
    cipher = SecretCipher(Fernet.generate_key().decode("ascii"))
    token = cipher.encrypt(KEY)
    assert KEY not in token
    assert cipher.decrypt(token) == KEY
    assert "redacted" in repr(cipher)


def test_cipher_rejects_missing_or_invalid_key() -> None:
    for bad in ("", "not-a-fernet-key"):
        with pytest.raises(SecretCipherConfigError):
            SecretCipher(bad)


def test_decrypt_with_other_key_raises_fixed_error() -> None:
    token = SecretCipher(Fernet.generate_key()).encrypt(KEY)
    with pytest.raises(SecretDecryptionError) as exc_info:
        SecretCipher(Fernet.generate_key()).decrypt(token)
    assert exc_info.value.__context__ is None or exc_info.value.__suppress_context__
    assert token not in str(exc_info.value)
