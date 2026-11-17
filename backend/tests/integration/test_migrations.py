"""Integration tests for the Alembic schema lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Inspector

from app.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]

INITIAL_REVISION = "fc993ac91694"
GOOGLE_GEMINI_REVISION = "d2f7e806032a"

HEAD_TABLES = {
    "users",
    "gemini_credentials",
    "revoked_app_sessions",
    "word_entries",
    "review_records",
    "generated_sentences",
}

INITIAL_TABLES = {
    "users",
    "oauth_credentials",
    "word_entries",
    "review_records",
    "generated_sentences",
}

OAUTH_CREDENTIAL_COLUMNS = {
    "id",
    "user_id",
    "access_token_encrypted",
    "refresh_token_encrypted",
    "access_token_expires_at",
    "granted_scopes",
    "client_id",
    "ext_agent_host_id",
    "updated_at",
}


@pytest.fixture
def migration_db(tmp_path: Path, monkeypatch) -> tuple[Config, str]:
    """Point Alembic at a fresh SQLite file and return (config, url)."""
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DB_URL", database_url)
    get_settings.cache_clear()
    yield Config(str(BACKEND_ROOT / "alembic.ini")), database_url
    get_settings.cache_clear()


def _unique_constraints(inspector: Inspector, table: str) -> set[tuple]:
    return {
        (constraint["name"], tuple(constraint["column_names"]))
        for constraint in inspector.get_unique_constraints(table)
    }


def _inspect(database_url: str, check) -> None:
    engine = create_engine(database_url)
    try:
        check(inspect(engine))
    finally:
        engine.dispose()


def test_head_migration_matches_models_and_downgrades_cleanly(
    migration_db: tuple[Config, str],
) -> None:
    """Head creates the Google + Gemini BYOK schema and is fully reversible."""
    config, database_url = migration_db

    command.upgrade(config, "head")

    def check_head(inspector: Inspector) -> None:
        tables = set(inspector.get_table_names())
        assert HEAD_TABLES.issubset(tables)
        assert "oauth_credentials" not in tables

        user_columns = {c["name"] for c in inspector.get_columns("users")}
        assert user_columns == {
            "id",
            "email",
            "name",
            "oidc_issuer",
            "oidc_subject",
            "created_at",
        }
        assert ("uq_user_oidc_subject", ("oidc_issuer", "oidc_subject")) in (
            _unique_constraints(inspector, "users")
        )
        assert ("uq_word_entry_user_word", ("user_id", "word_form")) in (
            _unique_constraints(inspector, "word_entries")
        )

        gemini_columns = {
            c["name"]: c for c in inspector.get_columns("gemini_credentials")
        }
        assert set(gemini_columns) == {
            "id",
            "user_id",
            "api_key_encrypted",
            "key_hint",
            "created_at",
            "updated_at",
        }
        assert not gemini_columns["api_key_encrypted"]["nullable"]
        assert not gemini_columns["key_hint"]["nullable"]
        assert ("user_id",) in {
            cols for _, cols in _unique_constraints(inspector, "gemini_credentials")
        }
        (fk,) = inspector.get_foreign_keys("gemini_credentials")
        assert fk["referred_table"] == "users"
        assert fk["constrained_columns"] == ["user_id"]
        assert fk["options"].get("ondelete") == "CASCADE"

        revoked_columns = {
            c["name"]: c for c in inspector.get_columns("revoked_app_sessions")
        }
        assert set(revoked_columns) == {"sid", "expires_at"}
        assert not revoked_columns["expires_at"]["nullable"]
        assert inspector.get_pk_constraint("revoked_app_sessions")[
            "constrained_columns"
        ] == ["sid"]
        assert ["expires_at"] in [
            index["column_names"]
            for index in inspector.get_indexes("revoked_app_sessions")
        ]

    _inspect(database_url, check_head)

    # Alembic autogenerate must find no drift between the migrated schema and
    # the ORM metadata registered by app.models.
    command.check(config)

    # Stepping back one revision restores the initial OpenAI schema.
    command.downgrade(config, INITIAL_REVISION)

    def check_initial(inspector: Inspector) -> None:
        tables = set(inspector.get_table_names())
        assert INITIAL_TABLES.issubset(tables)
        assert "gemini_credentials" not in tables
        assert "revoked_app_sessions" not in tables
        assert {
            c["name"] for c in inspector.get_columns("oauth_credentials")
        } == OAUTH_CREDENTIAL_COLUMNS
        assert "oidc_client_id" in {
            c["name"] for c in inspector.get_columns("users")
        }
        assert (
            "uq_user_oidc_subject",
            ("oidc_issuer", "oidc_client_id", "oidc_subject"),
        ) in _unique_constraints(inspector, "users")

    _inspect(database_url, check_initial)

    command.downgrade(config, "base")

    def check_base(inspector: Inspector) -> None:
        assert (HEAD_TABLES | INITIAL_TABLES).isdisjoint(
            inspector.get_table_names()
        )

    _inspect(database_url, check_base)


def test_upgrade_from_initial_keeps_existing_users_and_words(
    migration_db: tuple[Config, str],
) -> None:
    """A developer DB already at the initial revision upgrades without data loss."""
    config, database_url = migration_db
    command.upgrade(config, INITIAL_REVISION)

    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, name, oidc_issuer, "
                    "oidc_client_id, oidc_subject) VALUES "
                    "('u1', 'a@example.com', 'A', 'https://auth.openai.com', "
                    "'client', 'sub-1')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO word_entries (id, user_id, word_form, reading, "
                    "meaning, next_review_date, ease_factor, interval_days, "
                    "repetitions) VALUES ('w1', 'u1', '猫', 'ねこ', 'cat', "
                    "'2026-01-01', 2.5, 0, 0)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, GOOGLE_GEMINI_REVISION)

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            user = conn.execute(
                text("SELECT id, email, oidc_issuer, oidc_subject FROM users")
            ).one()
            word = conn.execute(
                text("SELECT id, user_id, word_form FROM word_entries")
            ).one()
    finally:
        engine.dispose()

    assert tuple(user) == ("u1", "a@example.com", "https://auth.openai.com", "sub-1")
    assert tuple(word) == ("w1", "u1", "猫")
