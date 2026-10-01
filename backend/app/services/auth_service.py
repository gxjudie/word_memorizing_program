"""Auth Service — login/logout and session (JWT) issuance.

The backend owns identity (the "opencode-style" auth decision): the Learner
authenticates to *our* application, not to the LLM provider, so the backend can
authorize the LLM proxy against the Learner's own account. This service is the
business-logic layer for that identity — it verifies credentials against the
``User`` ORM row and mints a stateless session token on success. It takes a
SQLAlchemy ``Session`` and talks to the model directly, but holds no HTTP
concerns (status codes, headers); the ``api/`` layer maps results and errors to
responses.

Sessions are **stateless JWTs** (see :mod:`app.auth.security`). That shapes two
behaviours:

* **login** verifies the password with constant-ish argon2 verification and,
  on success, issues a signed token carrying the user id.
* **logout** is a client-side token discard. There is no server session store
  to clear, so logout is a no-op here provided for API symmetry and as the
  seam where token revocation (a denylist) would later be added.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_password, issue_token, verify_password
from app.models.user import User

__all__ = [
    "AuthService",
    "AuthResult",
    "InvalidCredentialsError",
]


class InvalidCredentialsError(Exception):
    """Raised when login fails because the email or password is wrong.

    The same error is used whether the email is unknown or the password is
    incorrect, so callers cannot distinguish the two cases and probe for valid
    accounts (user enumeration).
    """


@dataclass(frozen=True)
class AuthResult:
    """The outcome of a successful login.

    Attributes:
        user_id: The authenticated Learner's id (the token subject).
        token: A signed JWT session token the client sends as a Bearer
            credential on subsequent requests.
    """

    user_id: str
    token: str


class AuthService:
    """Login/logout against the ``User`` model, issuing stateless sessions."""

    def __init__(self, db: Session) -> None:
        """Create the service.

        Args:
            db: An active SQLAlchemy session for reading the ``User`` row.
        """
        self._db = db

    def login(self, email: str, password: str) -> AuthResult:
        """Verify credentials and issue a session token.

        Args:
            email: The Learner's unique login email.
            password: The candidate plaintext password.

        Returns:
            An :class:`AuthResult` with the user id and a signed JWT.

        Raises:
            InvalidCredentialsError: If no user has that email, the account has
                no password set (OAuth-only), or the password does not match.
        """
        user = self._db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        # Reject unknown users and password-less (OAuth-only) accounts with the
        # same error so neither case is distinguishable to the caller.
        if user is None or user.password_hash is None:
            raise InvalidCredentialsError("Invalid email or password")

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password")

        return AuthResult(user_id=user.id, token=issue_token(user.id))

    def logout(self) -> None:
        """End the current session.

        Sessions are stateless JWTs with no server-side store, so logout is a
        client-side token discard and a no-op server-side. This method is the
        designated seam for future token revocation (e.g. a short-lived
        denylist) without changing the API surface.
        """
        return None

    def register(self, email: str, password: str) -> User:
        """Create a new Learner account with a hashed password.

        Provided so credentials can be seeded/created through the same security
        primitives the Auth Service verifies against (argon2 hashing). The
        caller is responsible for committing the transaction.

        Args:
            email: The new account's unique email.
            password: The plaintext password to hash and store.

        Returns:
            The newly created, not-yet-committed :class:`User`.
        """
        user = User(email=email, password_hash=hash_password(password))
        self._db.add(user)
        self._db.flush()
        return user
