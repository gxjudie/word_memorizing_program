"""FastAPI application factory.

Routers are registered here as the API layer is implemented in later tasks.

Key secrecy at the HTTP layer (Req 10.12, 10.13): FastAPI's default ``422``
body echoes each offending ``input`` (and ``ctx``), which could leak a
malformed Gemini key, so a ``RequestValidationError`` handler strips both
fields. Access logging records only method, path (no query string), and
status, never headers or bodies.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

access_logger = logging.getLogger("app.access")

# Error-entry fields that can carry submitted values.
_VALIDATION_FIELDS_TO_STRIP = frozenset({"input", "ctx"})


def _key_safe_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Return the validation errors without ``input`` and ``ctx``."""
    return [
        {k: v for k, v in error.items() if k not in _VALIDATION_FIELDS_TO_STRIP}
        for error in exc.errors()
    ]


async def key_safe_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """``422`` with the default ``{"detail": [...]}`` shape, minus submitted values."""
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(_key_safe_errors(exc))},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """At startup, remove ``jvt-anki-*`` temp directories left by a crash (Req 2.40).

    The store is resolved through ``dependency_overrides`` so a test's store
    (and its temp root) is the one swept.
    """
    from app.api.deps import get_preview_store

    provider = app.dependency_overrides.get(get_preview_store, get_preview_store)
    provider().remove_stale_workdirs()
    yield


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Japanese Vocabulary Trainer API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_exception_handler(RequestValidationError, key_safe_validation_handler)

    @app.middleware("http")
    async def access_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Log method, path, and status only."""
        response = await call_next(request)
        access_logger.info(
            "%s %s %s", request.method, request.url.path, response.status_code
        )
        return response

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    # Router registration. The review router is included here as the API layer
    # is built out in later tasks.
    from app.api.routes import auth, gemini_key, review, word_import, words

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(word_import.router, prefix="/api/words/import", tags=["word-import"])
    app.include_router(words.router, prefix="/api/words", tags=["words"])
    app.include_router(review.router, prefix="/api/review", tags=["review"])
    app.include_router(gemini_key.router, prefix="/api/gemini-key", tags=["gemini-key"])

    return app


app = create_app()
