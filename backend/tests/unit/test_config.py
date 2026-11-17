"""Focused tests for backend configuration and foundation imports."""

from __future__ import annotations

import tomllib
from pathlib import Path

from fastapi import FastAPI
from pydantic import SecretStr

from app.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_settings_read_google_session_encryption_and_gemini_environment(monkeypatch):
    values = {
        "DB_URL": "postgresql://learner@example.test/vocab",
        "GOOGLE_OAUTH_CLIENT_ID": "1234-abc.apps.googleusercontent.com",
        "GOOGLE_OAUTH_CLIENT_SECRET": "google-client-secret",
        "OAUTH_REDIRECT_URI": "https://trainer.example.test/api/auth/callback",
        "SESSION_SECRET": "application-session-secret",
        "TOKEN_ENCRYPTION_KEY": "fernet-encryption-key",
        "GEMINI_MODEL": "gemini-test-model",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert settings.db_url == values["DB_URL"]
    assert settings.google_oauth_client_id == values["GOOGLE_OAUTH_CLIENT_ID"]
    assert isinstance(settings.google_oauth_client_secret, SecretStr)
    assert (
        settings.google_oauth_client_secret.get_secret_value()
        == values["GOOGLE_OAUTH_CLIENT_SECRET"]
    )
    assert values["GOOGLE_OAUTH_CLIENT_SECRET"] not in repr(settings)
    assert settings.oauth_redirect_uri == values["OAUTH_REDIRECT_URI"]
    assert settings.session_secret == values["SESSION_SECRET"]
    assert settings.token_encryption_key == values["TOKEN_ENCRYPTION_KEY"]
    assert settings.gemini_model == values["GEMINI_MODEL"]


def test_settings_defaults_and_removed_fields(monkeypatch):
    for name in (
        "OAUTH_REDIRECT_URI",
        "GEMINI_MODEL",
        "OPENAI_OAUTH_CLIENT_ID",
        "EXT_AGENT_HOST_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    # Stale OpenAI-era variables in the environment are ignored.
    monkeypatch.setenv("OPENAI_OAUTH_CLIENT_ID", "stale")
    monkeypatch.setenv("EXT_AGENT_HOST_ID", "stale")

    settings = Settings(_env_file=None)

    assert settings.oauth_redirect_uri == "http://localhost:5173/api/auth/callback"
    assert settings.gemini_model == "gemini-flash-lite-latest"
    field_names = set(Settings.model_fields)
    assert "openai_oauth_client_id" not in field_names
    assert "ext_agent_host_id" not in field_names
    # No developer-held Gemini (or any provider) API key.
    assert not any("api_key" in name for name in field_names)
    assert "jwt_secret" not in field_names


def test_env_example_lists_exactly_the_settings_variables():
    lines = (BACKEND_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    names = {
        line.split("=", 1)[0].strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert names == {name.upper() for name in Settings.model_fields}


def test_pyproject_drops_openai_authlib_and_passlib():
    data = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = [d.lower() for d in data["project"]["dependencies"]]
    dev = [d.lower() for d in data["project"]["optional-dependencies"]["dev"]]
    for banned in ("openai", "authlib", "passlib"):
        assert not any(d.startswith(banned) for d in deps + dev)
    for required in (
        "fastapi",
        "uvicorn",
        "pydantic",
        "pydantic-settings",
        "sqlalchemy",
        "alembic",
        "httpx",
        "python-jose",
        "cryptography",
    ):
        assert any(d.startswith(required) for d in deps), required
    assert any(d.startswith("pytest") for d in dev)
    assert any(d.startswith("hypothesis") for d in dev)
    assert data["tool"]["hypothesis"]["max_examples"] >= 100


def test_foundation_modules_and_app_factory_import():
    from app.db.session import SessionLocal, engine
    from app.main import create_app

    app = create_app()

    assert isinstance(app, FastAPI)
    assert SessionLocal.kw["bind"] is engine
    assert any(route.path == "/health" for route in app.routes)
