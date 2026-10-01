"""FastAPI application factory.

Routers are registered here as the API layer is implemented in later tasks.
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Japanese Vocabulary Trainer API",
        version="0.1.0",
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    # Router registration. The review router is included here as the API layer
    # is built out in later tasks.
    from app.api.routes import auth, words

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(words.router, prefix="/api/words", tags=["words"])

    return app


app = create_app()
