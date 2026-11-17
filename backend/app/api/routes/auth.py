"""HTTP routes for "Sign in with Google" and the app's own session (Req 9).

``GET /login`` stores a fresh state, nonce, and PKCE verifier in an encrypted,
HttpOnly, SameSite=Lax auth-start cookie and redirects (302) to Google.
``GET /callback`` always deletes that cookie, delegates state verification,
the code exchange, and ID-token validation to ``AuthService``, and returns the
browser-navigation HTML bridge that stores only the App_Session in the SPA.
Google access and refresh tokens never cross this HTTP boundary (Req 9.12).

``POST /logout`` revokes the bearer session's ``sid`` until its expiry
(Req 9.13) and ``GET /me`` returns the Learner's identity plus the browser-safe
Gemini key status (connected + last-4 hint, Req 10.12).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from app.api.deps import (
    AuthServiceDep,
    CurrentUser,
    GeminiKeyServiceDep,
    SettingsDep,
    bearer_scheme,
)
from app.auth.oauth import (
    AUTH_START_COOKIE,
    AUTH_START_COOKIE_PATH,
    AUTHORIZATION_TTL,
    auth_start_cookie_is_secure,
    decode_auth_start_cookie,
    encode_auth_start_cookie,
)
from app.auth.session import InvalidSessionError, SessionClaims, decode_app_session
from app.config import Settings
from app.schemas.gemini_key import GeminiKeyStatusOut
from app.services.auth_service import (
    AccountVerificationError,
    AuthConfigurationError,
    AuthPersistenceError,
    AuthStateError,
    AuthorizationDeniedError,
    TokenExchangeError,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter()

_RETRY_URL = "/api/auth/login"

# Safe callback error codes rendered by the Login view. Each maps one failure
# class of Req 9 to a fixed status and message; none carries provider data.
_ERROR_LOGIN_NOT_VERIFIED = (  # Req 9.4: state missing/mismatched/expired/used
    status.HTTP_400_BAD_REQUEST,
    "login_not_verified",
    "Login could not be verified",
)
_ERROR_SIGN_IN_NOT_COMPLETED = (  # Req 9.5: Google error / cancel / deny
    status.HTTP_400_BAD_REQUEST,
    "sign_in_not_completed",
    "Sign-in was not completed",
)
_ERROR_TOKEN_EXCHANGE = (  # Req 9.7: timeout, network error, error, no id_token
    status.HTTP_503_SERVICE_UNAVAILABLE,
    "sign_in_service_unreachable",
    "Sign-in could not be completed",
)
_ERROR_ACCOUNT_NOT_VERIFIED = (  # Req 9.10: ID token / JWKS validation failure
    status.HTTP_401_UNAUTHORIZED,
    "account_not_verified",
    "Account could not be verified",
)
_ERROR_UNAVAILABLE = (  # server configuration or persistence failure
    status.HTTP_503_SERVICE_UNAVAILABLE,
    "sign_in_unavailable",
    "Sign-in is temporarily unavailable",
)


class LoginResponse(BaseModel):
    """The app's own bearer session, never a Google token."""

    access_token: str
    token_type: str = "bearer"


class AuthErrorResponse(BaseModel):
    """Safe callback error contract consumed by the login UI."""

    code: str
    message: str
    retry_url: str | None = None


class LogoutResponse(BaseModel):
    """Acknowledgement that the App_Session was ended."""

    success: bool = True


class MeResponse(BaseModel):
    """``GET /api/auth/me``: identity plus the browser-safe key status."""

    email: str
    name: str | None = None
    gemini_key: GeminiKeyStatusOut


# ---- helpers ----


def _cookie_is_secure(settings: Settings) -> bool:
    """Use Secure cookies whenever the configured public callback uses HTTPS."""

    return auth_start_cookie_is_secure(settings.oauth_redirect_uri)


def _cipher(settings: Settings) -> Fernet:
    """Fernet cipher for the auth-start cookie (``TOKEN_ENCRYPTION_KEY``)."""

    try:
        return Fernet(settings.token_encryption_key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        raise AuthConfigurationError("TOKEN_ENCRYPTION_KEY is invalid") from None


def _missing_configuration(settings: Settings) -> list[str]:
    """Names (never values) of the sign-in settings that are not usable."""

    missing: list[str] = []
    if not settings.google_oauth_client_id:
        missing.append("GOOGLE_OAUTH_CLIENT_ID")
    if not settings.google_oauth_client_secret.get_secret_value():
        missing.append("GOOGLE_OAUTH_CLIENT_SECRET")
    try:
        _cipher(settings)
    except AuthConfigurationError:
        missing.append("TOKEN_ENCRYPTION_KEY")
    return missing


def _delete_auth_start_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        AUTH_START_COOKIE,
        path=AUTH_START_COOKIE_PATH,
        secure=_cookie_is_secure(settings),
        httponly=True,
        samesite="lax",
    )


def _is_browser_navigation(request: Request) -> bool:
    """Return true only for a browser document navigation, not API fetches."""

    return (
        request.headers.get("sec-fetch-mode", "").lower() == "navigate"
        and request.headers.get("sec-fetch-dest", "").lower() == "document"
    )


_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _spa_bridge_response(
    *,
    settings: Settings,
    session_token: str | None = None,
    error_code: str | None = None,
) -> HTMLResponse:
    """Store only the first-party session and return the browser to the SPA.

    The callback is reached through Vite's same-origin ``/api`` proxy. This
    tiny no-store document completes the browser navigation without putting
    the App_Session in a URL. Google tokens never enter this response.
    """

    token_json = json.dumps(session_token)
    error_json = json.dumps(error_code)
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>로그인 처리 중</title>
</head>
<body>
  <p>로그인 결과를 앱으로 전달하고 있습니다…</p>
  <script>
    (() => {{
      const token = {token_json};
      const error = {error_json};
      try {{
        if (token) localStorage.setItem('jvt.token', token);
        else localStorage.removeItem('jvt.token');
        const result = token ? '?auth=success' : `?auth_error=${{encodeURIComponent(error || 'sign_in_unavailable')}}`;
        window.location.replace('/' + result);
      }} catch (_) {{
        window.location.replace('/?auth_error=sign_in_unavailable');
      }}
    }})();
  </script>
</body>
</html>"""
    response = HTMLResponse(
        content=html,
        status_code=status.HTTP_200_OK,
        headers=_NO_STORE_HEADERS,
    )
    _delete_auth_start_cookie(response, settings)
    return response


def _configuration_error_page(settings: Settings) -> HTMLResponse:
    """Static 503 page shown instead of redirecting when sign-in is not set up."""

    html = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>로그인을 사용할 수 없습니다</title>
</head>
<body>
  <h1>지금은 로그인할 수 없어요</h1>
  <p>서버의 Google 로그인 설정이 완료되지 않았습니다. 관리자에게 문의하세요.</p>
  <p><a href="/">앱으로 돌아가기</a></p>
</body>
</html>"""
    response = HTMLResponse(
        content=html,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers=_NO_STORE_HEADERS,
    )
    _delete_auth_start_cookie(response, settings)
    return response


def _error_response(
    error: tuple[int, str, str],
    *,
    settings: Settings,
    request: Request,
    retry: bool = True,
) -> Response:
    """Browser navigations go through the bridge; API fetches get JSON."""

    status_code, code, message = error
    if _is_browser_navigation(request):
        return _spa_bridge_response(settings=settings, error_code=code)

    body = AuthErrorResponse(
        code=code,
        message=message,
        retry_url=_RETRY_URL if retry else None,
    )
    response = JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers={"Cache-Control": "no-store"},
    )
    _delete_auth_start_cookie(response, settings)
    return response


def get_current_session_claims(
    _user: CurrentUser,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    settings: SettingsDep,
) -> SessionClaims:
    """Claims of the valid, non-revoked bearer App_Session.

    ``get_current_user`` has already rejected missing, invalid, expired, and
    revoked sessions with the same 401, so decoding here cannot fail in
    practice; the guard keeps the 401 contract if it ever does.
    """

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:  # pragma: no cover - guarded by get_current_user
        raise unauthorized
    try:
        return decode_app_session(
            credentials.credentials, secret=settings.session_secret
        )
    except InvalidSessionError:  # pragma: no cover - guarded by get_current_user
        raise unauthorized from None


CurrentSessionClaims = Annotated[SessionClaims, Depends(get_current_session_claims)]


# ---- routes ----


@router.get("/login", response_model=None)
def login(
    request: Request,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> Response:
    """Start Google Authorization Code + PKCE + OIDC sign-in (Req 9.1, 9.2)."""

    missing = _missing_configuration(settings)
    if missing:
        # Variable names only; never a secret value.
        logger.warning(
            "Google sign-in is not configured; missing or invalid: %s",
            ", ".join(missing),
        )
        if _is_browser_navigation(request):
            return _configuration_error_page(settings)
        return _error_response(
            _ERROR_UNAVAILABLE, settings=settings, request=request, retry=False
        )

    try:
        start = auth_service.build_authorization_url()
        encoded_start = encode_auth_start_cookie(start, _cipher(settings))
    except AuthConfigurationError:
        logger.warning("Google sign-in could not start: configuration error")
        if _is_browser_navigation(request):
            return _configuration_error_page(settings)
        return _error_response(
            _ERROR_UNAVAILABLE, settings=settings, request=request, retry=False
        )

    response = RedirectResponse(
        url=start.authorization_url,
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        AUTH_START_COOKIE,
        encoded_start,
        max_age=int(AUTHORIZATION_TTL.total_seconds()),
        path=AUTH_START_COOKIE_PATH,
        secure=_cookie_is_secure(settings),
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback", response_model=None)
async def callback(
    request: Request,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    """Handle Google's redirect and return the App_Session via the bridge.

    Every outcome deletes the auth-start cookie. ``AuthService`` rejects a
    provider error first (Req 9.5), then a bad state before any token
    exchange (Req 9.3, 9.4), then exchange and ID-token failures (Req 9.7,
    9.10). No App_Session exists on any failure.
    """

    encoded_start = request.cookies.get(AUTH_START_COOKIE)
    try:
        expected = (
            decode_auth_start_cookie(encoded_start, _cipher(settings))
            if encoded_start
            else None
        )
        app_session = await auth_service.handle_callback(
            code=code,
            state=state,
            error=error,
            expected=expected,
        )
    except AuthorizationDeniedError:
        return _error_response(
            _ERROR_SIGN_IN_NOT_COMPLETED, settings=settings, request=request
        )
    except AuthStateError:
        return _error_response(
            _ERROR_LOGIN_NOT_VERIFIED, settings=settings, request=request
        )
    except TokenExchangeError:
        return _error_response(_ERROR_TOKEN_EXCHANGE, settings=settings, request=request)
    except AccountVerificationError:
        return _error_response(
            _ERROR_ACCOUNT_NOT_VERIFIED, settings=settings, request=request
        )
    except (AuthConfigurationError, AuthPersistenceError) as exc:
        logger.warning("Google sign-in failed: %s", type(exc).__name__)
        return _error_response(_ERROR_UNAVAILABLE, settings=settings, request=request)

    if _is_browser_navigation(request):
        return _spa_bridge_response(settings=settings, session_token=app_session.token)

    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content=LoginResponse(access_token=app_session.token).model_dump(),
        headers={"Cache-Control": "no-store"},
    )
    _delete_auth_start_cookie(response, settings)
    return response


@router.post("/logout", response_model=LogoutResponse)
def logout(
    claims: CurrentSessionClaims,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> Response:
    """Revoke the bearer App_Session's ``sid`` until its ``exp`` (Req 9.13)."""

    try:
        auth_service.logout(claims.session_id, claims.expires_at)
    except AuthPersistenceError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"code": "logout_failed", "message": "Logout could not be saved"},
            headers={"Cache-Control": "no-store"},
        )

    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content=LogoutResponse().model_dump(),
        headers={"Cache-Control": "no-store"},
    )
    _delete_auth_start_cookie(response, settings)
    return response


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser, key_service: GeminiKeyServiceDep) -> MeResponse:
    """Identity plus ``{connected, key_hint}``; never the key (Req 10.12)."""

    key_status = key_service.status(user.id)
    return MeResponse(
        email=user.email,
        name=user.name,
        gemini_key=GeminiKeyStatusOut(
            connected=key_status.connected, key_hint=key_status.key_hint
        ),
    )
