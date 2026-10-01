"""Application configuration.

Settings are read from environment variables (and an optional ``.env`` file).
See ``.env.example`` for the required variables.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the backend.

    Attributes:
        db_url: SQLAlchemy database URL. PostgreSQL in production; SQLite is
            acceptable for local/dev and tests.
        openai_api_key: Server-held OpenAI key used by the LLM proxy. The
            browser never receives this value.
        jwt_secret: Secret used to sign JWT session tokens.
    """

    db_url: str = "sqlite:///./jvt.db"
    openai_api_key: str = ""
    jwt_secret: str = "change-me"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
