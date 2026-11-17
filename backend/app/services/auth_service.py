"""Sign in with Google: callback verification, account upsert, and logout.

Google sign-in establishes identity only (Req 9). The Google access token,
any refresh token, and the raw token response live only in local variables
of :meth:`AuthService.handle_callback` and are never persisted or logged
(Req 9.12). Sentence generation uses each Learner's own Gemini key instead.
"""
from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.oauth import (
    AuthStart,
    ConsumedStateRegistry,
    GoogleOAuthClient,
    OAuthProtocolError,
    OAuthTransportError,
    create_authorization_start,
)
from app.auth.oidc import (
    IDTokenValidationError,
    unverified_key_id,
    validate_google_id_token,
)
from app.auth.session import AppSession, issue_app_session, revoke_app_session
from app.config import Settings, get_settings
from app.models.user import User

__all__ = [
    "AuthService",
    "AuthError",
    "AuthConfigurationError",
    "AuthStateError",
    "AuthorizationDeniedError",
    "TokenExchangeError",
    "AccountVerificationError",
    "AuthPersistenceError",
    "default_state_registry",
]


class AuthError(Exception):
    """Base class for safe, user-facing authentication failures."""


class AuthConfigurationError(AuthError):
    """The server is missing Google OAuth configuration."""


class AuthStateError(AuthError):
    """Req 9.4: state missing, mismatched, expired, or already used."""


class AuthorizationDeniedError(AuthError):
    """Req 9.5: Google returned an error or the Learner cancelled/denied."""


class TokenExchangeError(AuthError):
    """Req 9.7: the code exchange timed out, failed, or had no ID token."""


class AccountVerificationError(AuthError):
    """Req 9.10: the Google ID token (or its JWKS) could not be verified."""


class AuthPersistenceError(AuthError):
    """The verified account could not be saved."""


# Process-wide registry so a replayed auth-start cookie is rejected across
# requests (each request gets a fresh AuthService).
default_state_registry = ConsumedStateRegistry()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AuthService:
    """Coordinate Google OAuth/OIDC sign-in, account association, and logout."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        oauth_client: GoogleOAuthClient | None = None,
        state_registry: ConsumedStateRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._oauth_client = oauth_client
        self._state_registry = (
            state_registry if state_registry is not None else default_state_registry
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return _aware_utc(self._clock())

    def _client(self) -> GoogleOAuthClient:
        if self._oauth_client is None:
            secret = self._settings.google_oauth_client_secret.get_secret_value()
            if not self._settings.google_oauth_client_id or not secret:
                raise AuthConfigurationError("Google sign-in is not configured")
            self._oauth_client = GoogleOAuthClient(
                client_id=self._settings.google_oauth_client_id,
                client_secret=secret,
                redirect_uri=self._settings.oauth_redirect_uri,
            )
        return self._oauth_client

    # ---- Login (Req 9.1, 9.2, 9.11) ----

    def build_authorization_url(self) -> AuthStart:
        """Return a fresh Google authorization start (state, nonce, PKCE)."""

        if not self._settings.google_oauth_client_id:
            raise AuthConfigurationError("Google sign-in is not configured")
        try:
            return create_authorization_start(
                client_id=self._settings.google_oauth_client_id,
                redirect_uri=self._settings.oauth_redirect_uri,
                now=self._now(),
            )
        except ValueError:
            raise AuthConfigurationError("Google sign-in is not configured") from None

    # ---- Callback (Req 9.3-9.12) ----

    def _verify_state(
        self, state: str | None, expected: AuthStart | None
    ) -> AuthStart:
        """Req 9.3/9.4: present, matching, unexpired, unused; then mark used."""

        now = self._now()
        if (
            expected is None
            or not state
            or not expected.state
            or not hmac.compare_digest(state.encode("utf-8"), expected.state.encode("utf-8"))
            or now >= _aware_utc(expected.expires_at)
        ):
            raise AuthStateError("Login could not be verified")
        if not self._state_registry.consume(
            expected.state, expires_at=expected.expires_at, now=now
        ):
            raise AuthStateError("Login could not be verified")
        return expected

    async def handle_callback(
        self,
        *,
        code: str | None,
        state: str | None,
        error: str | None,
        expected: AuthStart | None,
    ) -> AppSession:
        """Verify the callback, validate the identity, and issue an App_Session.

        The caller must delete the auth-start cookie regardless of outcome.
        """

        if error:
            raise AuthorizationDeniedError("Sign-in was not completed")
        verified = self._verify_state(state, expected)
        if not code:
            raise AuthorizationDeniedError("Sign-in was not completed")

        client = self._client()

        # Req 9.6/9.7: exactly one exchange. The token response, including any
        # access_token / refresh_token, stays in this local scope only.
        try:
            token_response = await client.exchange_code(
                code=code, code_verifier=verified.code_verifier
            )
        except (OAuthTransportError, OAuthProtocolError):
            raise TokenExchangeError("Sign-in could not be completed") from None
        id_token = token_response["id_token"]
        del token_response

        # Req 9.8/9.10: validate before any account write.
        try:
            jwks = await client.get_jwks(kid=unverified_key_id(id_token))
            identity = validate_google_id_token(
                id_token,
                jwks,
                client_id=self._settings.google_oauth_client_id,
                expected_nonce=verified.nonce,
                now=self._now(),
            )
        except (IDTokenValidationError, OAuthTransportError, OAuthProtocolError):
            raise AccountVerificationError("Account could not be verified") from None

        # Req 9.9: account keyed by (normalized issuer, subject), not email.
        try:
            user = self._db.execute(
                select(User).where(
                    User.oidc_issuer == identity.issuer,
                    User.oidc_subject == identity.subject,
                )
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    email=identity.email,
                    name=identity.name,
                    oidc_issuer=identity.issuer,
                    oidc_subject=identity.subject,
                )
                self._db.add(user)
            else:
                user.email = identity.email
                user.name = identity.name
            self._db.commit()
            user_id = user.id
        except SQLAlchemyError:
            self._db.rollback()
            raise AuthPersistenceError("Account could not be saved") from None

        return issue_app_session(
            user_id,
            secret=self._settings.session_secret,
            now=self._now(),
        )

    # ---- Logout (Req 9.13) ----

    def logout(self, session_id: str, expires_at: datetime) -> None:
        """Revoke the App_Session ``sid`` until its original expiry."""

        try:
            revoke_app_session(self._db, session_id, expires_at, now=self._now())
        except SQLAlchemyError:
            self._db.rollback()
            raise AuthPersistenceError("Logout could not be saved") from None
