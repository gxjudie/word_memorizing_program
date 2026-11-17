# Japanese Vocabulary Trainer — Backend

FastAPI + Python backend for the Japanese Vocabulary Trainer.

## Layout

```
app/
  main.py                # FastAPI app factory + router registration
  config.py              # DB, Google OAuth/OIDC, app-session, token-encryption, Gemini model settings
  api/routes/            # HTTP layer only (auth, words, review, gemini_key)
  schemas/               # Pydantic request/response + LLM sentence schema
  models/                # SQLAlchemy ORM tables
  services/              # business logic (word, review, scheduler, sentence generation)
  auth/                  # OAuth/PKCE, OIDC validation, and first-party app sessions
  db/session.py          # SQLAlchemy engine / session factory
tests/
  properties/            # Hypothesis property tests
  unit/                  # example / edge-case tests
  integration/           # provider boundary, DB, auth-protected routes
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then edit values
```

## Run

```bash
uvicorn app.main:app --reload
```

Interactive API docs are served at `/docs`.

## Test

```bash
pytest
```

Property-based tests use Hypothesis with a minimum of 100 examples.
