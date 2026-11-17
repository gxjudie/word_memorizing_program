"""Application configuration loaded from environment variables.

Sign-in uses Google OAuth 2.0 / OIDC for identity only, followed by the
application's own signed session. Sentence generation uses each Learner's own
Gemini API key (BYOK), stored encrypted per user, so there is deliberately no
developer-held Gemini key setting here. Google and Gemini endpoint URLs are
code constants, not configuration.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for persistence, Google sign-in, sessions, and Gemini.

    An empty ``google_oauth_client_id``, ``google_oauth_client_secret``, or
    ``token_encryption_key`` is treated as a configuration error by the auth
    and key routes rather than failing at startup.
    """

    db_url: str = "sqlite:///./jvt.db"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr = SecretStr("")
    oauth_redirect_uri: str = "http://localhost:5173/api/auth/callback"
    session_secret: str = "change-me"
    token_encryption_key: str = ""
    gemini_model: str = "gemini-flash-lite-latest"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()
