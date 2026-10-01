"""Unit tests for the auth security primitives (hashing + JWT)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.auth.security import (
    InvalidTokenError,
    hash_password,
    issue_token,
    verify_password,
    verify_token,
)


def test_hash_password_round_trips():
    """A hashed password verifies against its own hash."""
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"  # not stored in plaintext
    assert verify_password("correct horse battery staple", hashed) is True


def test_hash_password_rejects_wrong_password():
    """Verification fails for a different password."""
    hashed = hash_password("s3cret")
    assert verify_password("not-the-password", hashed) is False


def test_hash_password_is_salted():
    """Hashing the same password twice yields different digests."""
    assert hash_password("same") != hash_password("same")


def test_verify_password_on_malformed_hash_is_false():
    """A non-match (not an exception) for an unrecognizable stored hash."""
    assert verify_password("whatever", "not-a-real-hash") is False


def test_issue_and_verify_token_round_trips():
    """A freshly issued token verifies back to its subject."""
    token = issue_token("user-123")
    assert verify_token(token) == "user-123"


def test_verify_token_rejects_tampered_token():
    """A token with a flipped character fails verification."""
    token = issue_token("user-123")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(InvalidTokenError):
        verify_token(tampered)


def test_verify_token_rejects_expired_token():
    """A token issued already-expired is rejected."""
    token = issue_token("user-123", expires_in=timedelta(seconds=-1))
    with pytest.raises(InvalidTokenError):
        verify_token(token)


def test_verify_token_rejects_garbage():
    """A non-JWT string is rejected rather than returning a subject."""
    with pytest.raises(InvalidTokenError):
        verify_token("this.is.not-a-jwt")
