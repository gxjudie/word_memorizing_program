"""Google OAuth 2.0 Authorization Code + PKCE primitives (Req 9.1-9.7, 9.11).

This module holds the provider-facing mechanics of "Sign in with Google":
fresh state / nonce / PKCE generation, the authorization URL, the encrypted
auth-start cookie payload, the consumed-state registry, the one-shot code
exchange, and the cached JWKS fetch. It has no FastAPI or database
dependencies, so every piece is testable with ``httpx.MockTransport``.

Google sign-in is used for identity only: the only scopes requested are
``openid email profile`` and ``access_type=offline`` is never sent.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken

__all__ = [
    "GOOGLE_ISSUER",
    "GOOGLE_ISSUERS",
    "GOOGLE_AUTHORIZATION_ENDPOINT",
    "GOOGLE_TOKEN_ENDPOINT",
    "GOOGLE_JWKS_URI",
    "GOOGLE_SCOPES",
    "AUTHORIZATION_TTL",
    "TOKEN_REQUEST_TIMEOUT_SECONDS",
    "AUTH_START_COOKIE",
    "AUTH_START_COOKIE_PATH",
    "OAuthProtocolError",
    "OAuthTransportError",
    "PKCEPair",
    "AuthStart",
    "generate_pkce_pair",
    "pkce_challenge",
    "create_authorization_start",
    "encode_auth_start_cookie",
    "decode_auth_start_cookie",
    "auth_start_cookie_is_secure",
    "ConsumedStateRegistry",
    "JWKSCache",
    "GoogleOAuthClient",
]

# ---- Google endpoint constants (code constants, not settings) ----
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

# Req 9.2 / 9.11: exactly these scopes, nothing more.
GOOGLE_SCOPES = ("openid", "email", "profile")

AUTHORIZATION_TTL = timedelta(minutes=10)
TOKEN_REQUEST_TIMEOUT_SECONDS = 30.0
JWKS_REQUEST_TIMEOUT_SECONDS = 10.0

AUTH_START_COOKIE = "jvt_oauth_start"
AUTH_START_COOKIE_PATH = "/api/auth"

_MAX_AGE_RE = re.compile(r"(?:^|,)\s*max-age\s*=\s*(\d+)\s*(?:,|$)", re.IGNORECASE)


class OAuthProtocolError(Exception):
    """The provider response did not satisfy the OAuth/OIDC contract."""


class OAuthTransportError(Exception):
    """The provider could not be reached in time or returned a server failure."""


@dataclass(frozen=True)
class PKCEPair:
    """A PKCE verifier (43-128 chars) and its S256 challenge."""

    verifier: str
    challenge: str


@dataclass(frozen=True)
class AuthStart:
    """Server-side values for one short-lived authorization attempt.

    ``authorization_url`` is empty when the value was decoded from the
    auth-start cookie, which carries only state, nonce, verifier, and expiry.
    """

    state: str
    nonce: str
    code_verifier: str
    expires_at: datetime
    authorization_url: str = ""

    @property
    def code_challenge(self) -> str:
        return pkce_challenge(self.code_verifier)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def pkce_challenge(verifier: str) -> str:
    """Return the RFC 7636 S256 challenge for ``verifier``."""

    return _base64url(hashlib.sha256(verifier.encode("ascii")).digest())


def generate_pkce_pair() -> PKCEPair:
    """Generate an RFC 7636 verifier (86 chars) and its S256 challenge."""

    verifier = secrets.token_urlsafe(64)
    return PKCEPair(verifier=verifier, challenge=pkce_challenge(verifier))


def create_authorization_start(
    *,
    client_id: str,
    redirect_uri: str,
    now: datetime | None = None,
) -> AuthStart:
    """Create fresh state, nonce, and PKCE values plus the Google auth URL.

    Every call generates new values (Req 9.1); none is reused across login
    attempts. The attempt is valid for 10 minutes.
    """

    if not client_id:
        raise ValueError("client_id is required")
    if not redirect_uri:
        raise ValueError("redirect_uri is required")

    pkce = generate_pkce_pair()
    state = _base64url(secrets.token_bytes(32))
    nonce = _base64url(secrets.token_bytes(32))
    issued_at = _aware_utc(now or datetime.now(timezone.utc))
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return AuthStart(
        state=state,
        nonce=nonce,
        code_verifier=pkce.verifier,
        expires_at=issued_at + AUTHORIZATION_TTL,
        authorization_url=f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}",
    )


# ---- Encrypted auth-start cookie ----


def auth_start_cookie_is_secure(redirect_uri: str) -> bool:
    """The cookie is ``Secure`` whenever the public callback uses HTTPS."""

    return urlsplit(redirect_uri).scheme.lower() == "https"


def encode_auth_start_cookie(start: AuthStart, cipher: Fernet) -> str:
    """Fernet-encrypt state, nonce, verifier, and expiry for the cookie."""

    payload = {
        "state": start.state,
        "nonce": start.nonce,
        "code_verifier": start.code_verifier,
        "expires_at": _aware_utc(start.expires_at).isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return cipher.encrypt(raw).decode("ascii")


def decode_auth_start_cookie(value: str | None, cipher: Fernet) -> AuthStart | None:
    """Decrypt the auth-start cookie; return ``None`` if absent or untrusted."""

    if not value:
        return None
    try:
        raw = cipher.decrypt(value.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        fields = ("state", "nonce", "code_verifier", "expires_at")
        if not all(isinstance(payload.get(name), str) for name in fields):
            return None
        return AuthStart(
            state=payload["state"],
            nonce=payload["nonce"],
            code_verifier=payload["code_verifier"],
            expires_at=_aware_utc(datetime.fromisoformat(payload["expires_at"])),
        )
    except (InvalidToken, TypeError, ValueError, UnicodeError):
        return None


# ---- Consumed-state registry (Req 9.3, 9.4, 9.6) ----


class ConsumedStateRegistry:
    """Remember accepted states until they expire so a replay is rejected.

    Only a SHA-256 hash of each state is kept. The registry is in-process with
    a TTL, which fits the single-process dev server; a multi-instance
    deployment would back it with a table.
    """

    def __init__(self) -> None:
        self._entries: dict[str, datetime] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    def consume(self, state: str, *, expires_at: datetime, now: datetime) -> bool:
        """Mark ``state`` used. Return ``False`` if it was already used."""

        now = _aware_utc(now)
        digest = self._digest(state)
        with self._lock:
            for key in [k for k, exp in self._entries.items() if exp <= now]:
                del self._entries[key]
            if digest in self._entries:
                return False
            self._entries[digest] = _aware_utc(expires_at)
            return True

    def is_consumed(self, state: str, *, now: datetime) -> bool:
        now = _aware_utc(now)
        with self._lock:
            expiry = self._entries.get(self._digest(state))
            return expiry is not None and expiry > now


# ---- JWKS cache ----


def _max_age(cache_control: str | None) -> int:
    if not cache_control:
        return 0
    lowered = cache_control.lower()
    if "no-store" in lowered or "no-cache" in lowered:
        return 0
    match = _MAX_AGE_RE.search(cache_control)
    return int(match.group(1)) if match else 0


class JWKSCache:
    """Google JWKS cached per ``Cache-Control: max-age``."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._jwks: dict[str, Any] | None = None
        self._expires_at = 0.0

    def get_cached(self) -> dict[str, Any] | None:
        if self._jwks is not None and self._monotonic() < self._expires_at:
            return self._jwks
        return None

    def store(self, jwks: dict[str, Any], cache_control: str | None) -> None:
        self._jwks = jwks
        self._expires_at = self._monotonic() + _max_age(cache_control)

    def clear(self) -> None:
        self._jwks = None
        self._expires_at = 0.0


def _jwks_has_kid(jwks: Mapping[str, Any], kid: str | None) -> bool:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        return False
    return any(isinstance(k, dict) and k.get("kid") == kid for k in keys)


_DEFAULT_JWKS_CACHE = JWKSCache()


class GoogleOAuthClient:
    """Async client for Google's token endpoint and JWKS.

    An ``httpx.AsyncClient`` may be injected (tests use ``MockTransport``);
    otherwise a short-lived client is created per call. Error messages are
    fixed strings and the original httpx exception is dropped (``from None``)
    so the client secret, code, or tokens never reach a log or traceback.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
        jwks_cache: JWKSCache | None = None,
        token_timeout: float = TOKEN_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http_client
        self._jwks_cache = jwks_cache if jwks_cache is not None else _DEFAULT_JWKS_CACHE
        self._token_timeout = token_timeout

    async def _send(self, method: str, url: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        async def _do(client: httpx.AsyncClient) -> httpx.Response:
            return await client.request(method, url, timeout=timeout, **kwargs)

        try:
            if self._http is not None:
                return await asyncio.wait_for(_do(self._http), timeout)
            async with httpx.AsyncClient() as client:
                return await asyncio.wait_for(_do(client), timeout)
        except (asyncio.TimeoutError, httpx.HTTPError):
            raise OAuthTransportError("Google could not be reached") from None

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange the authorization code exactly once (Req 9.6, 9.7).

        Returns the token response, which is guaranteed to contain a
        non-empty ``id_token``. The caller must treat the whole response as
        transient and never persist or log it (Req 9.12).
        """

        response = await self._send(
            "POST",
            GOOGLE_TOKEN_ENDPOINT,
            timeout=self._token_timeout,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": self._redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"accept": "application/json"},
        )
        if not response.is_success:
            raise OAuthProtocolError("Google rejected the token request")
        try:
            payload = response.json()
        except ValueError:
            raise OAuthProtocolError("Google returned an invalid token response") from None
        if not isinstance(payload, dict):
            raise OAuthProtocolError("Google returned an invalid token response")
        id_token = payload.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OAuthProtocolError("Google token response has no ID token")
        return payload

    async def _fetch_jwks(self) -> dict[str, Any]:
        response = await self._send(
            "GET",
            GOOGLE_JWKS_URI,
            timeout=JWKS_REQUEST_TIMEOUT_SECONDS,
            headers={"accept": "application/json"},
        )
        if not response.is_success:
            raise OAuthTransportError("Google signing keys are unavailable")
        try:
            payload = response.json()
        except ValueError:
            raise OAuthProtocolError("Google returned invalid signing keys") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise OAuthProtocolError("Google returned invalid signing keys")
        self._jwks_cache.store(payload, response.headers.get("cache-control"))
        return payload

    async def get_jwks(self, *, kid: str | None = None) -> dict[str, Any]:
        """Return Google's JWKS, fetching at most once per call.

        A fresh cache entry that contains ``kid`` is returned as-is. An empty
        or expired cache, or a cached set without ``kid`` (Google rotated its
        keys), triggers exactly one fetch; if the key is still missing the
        ID-token validator rejects the token.
        """

        cached = self._jwks_cache.get_cached()
        if cached is not None and (kid is None or _jwks_has_kid(cached, kid)):
            return cached
        return await self._fetch_jwks()
