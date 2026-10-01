"""Auth routes — login and logout (Auth model).

These routes are the HTTP seam over :class:`~app.services.auth_service.AuthService`.
The backend owns identity (the "opencode-style" auth decision): the Learner
authenticates to *our* application and receives a stateless session JWT, which
subsequent requests send as a Bearer credential.

The router holds only HTTP concerns — request/response shapes and status-code
mapping — and delegates all verification and token issuance to the service:

* ``POST /api/auth/login`` verifies ``email`` + ``password`` and returns the
  signed token; invalid credentials map to ``401`` (the same response whether
  the email is unknown or the password is wrong, so clients cannot probe for
  valid accounts).
* ``POST /api/auth/logout`` ends the session. Sessions are stateless JWTs with
  no server-side store, so this is a client-side token discard surfaced here
  for API symmetry; it always succeeds.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession
from app.services.auth_service import AuthService, InvalidCredentialsError

__all__ = ["router"]

router = APIRouter()


class LoginRequest(BaseModel):
    """Login request body.

    Attributes:
        email: The Learner's unique login email.
        password: The candidate plaintext password.
    """

    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    """Successful-login response.

    Attributes:
        access_token: The signed JWT session token to send as a Bearer
            credential on subsequent requests.
        token_type: The credential scheme; always ``"bearer"``.
    """

    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    """Logout response acknowledging the (client-side) session end."""

    success: bool = True


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: DbSession) -> LoginResponse:
    """Authenticate a Learner and issue a session token.

    Args:
        body: The ``email`` + ``password`` credentials.
        db: The request-scoped database session.

    Returns:
        A :class:`LoginResponse` carrying the signed JWT.

    Raises:
        HTTPException: ``401 Unauthorized`` when the credentials are invalid.
            The same response is returned for an unknown email, a password-less
            (OAuth-only) account, and a wrong password, so the cases are
            indistinguishable to the client.
    """
    service = AuthService(db)
    try:
        result = service.login(body.email, body.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return LoginResponse(access_token=result.token)


@router.post("/logout", response_model=LogoutResponse)
def logout(db: DbSession) -> LogoutResponse:
    """End the current session.

    Sessions are stateless JWTs, so there is no server session store to clear;
    the client discards its token. This endpoint always succeeds and exists for
    API symmetry (and as the seam where token revocation would later be added).

    Args:
        db: The request-scoped database session.

    Returns:
        A :class:`LogoutResponse` with ``success=True``.
    """
    AuthService(db).logout()
    return LogoutResponse()
