"""Unit tests for the Gemini sentence-generation adapter (task 6.2).

Gemini is always mocked with ``httpx.MockTransport``; no test opens a real
network connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Callable

import httpx
import pytest

from app.schemas.sentence import GEMINI_RESPONSE_SCHEMA, GeneratedSentenceSchema
from app.services.gemini_errors import GeminiFailure, classify_generation_failure
from app.services.sentence_generation.base import (
    GenerationError,
    GenerationTimeout,
    InvalidGeminiKeyError,
    MissingGeminiKeyError,
    QuotaExceededError,
)
from app.services.sentence_generation.gemini_adapter import (
    GEMINI_API_BASE_URL,
    SYSTEM_INSTRUCTION,
    GeminiAdapter,
)

API_KEY = "AIza" + "x" * 35
MODEL = "gemini-flash-lite-latest"
TARGET = SimpleNamespace(word_form="勉強", reading="べんきょう", meaning="study")


def _sentence(non_target: str = "毎日", *, with_target: bool = True) -> dict:
    tokens = [
        {
            "surface": non_target,
            "reading": "まいにち",
            "meaning": "every day",
            "is_target": False,
            "contains_kanji": True,
        },
        {
            "surface": "勉強",
            "reading": "べんきょう",
            "meaning": "study",
            "is_target": with_target,
            "contains_kanji": True,
        },
    ]
    return {"text": f"{non_target}勉強する。", "tokens": tokens}


def _ok(sentence: dict | str, *, finish: str = "STOP") -> httpx.Response:
    text = sentence if isinstance(sentence, str) else json.dumps(
        sentence, ensure_ascii=False
    )
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": text}]},
                 "finishReason": finish}
            ]
        },
    )


def _rpc_error(code: int, status: str, reason: str | None = None) -> httpx.Response:
    error: dict = {"code": code, "message": f"bad key {API_KEY}", "status": status}
    if reason:
        error["details"] = [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}
        ]
    return httpx.Response(code, json={"error": error})


class Recorder:
    """MockTransport handler that replays a scripted response sequence."""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    key_provider: Callable[[str], str] = lambda _user_id: API_KEY,
    **kwargs,
) -> GeminiAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiAdapter(key_provider, client, model=MODEL, **kwargs)


def _run(adapter: GeminiAdapter, previous=None):
    return asyncio.run(adapter.generate("learner-1", TARGET, previous))


def test_request_shape_uses_learner_key_header_and_structured_output() -> None:
    recorder = Recorder([_ok(_sentence())])
    seen_users: list[str] = []

    def key_provider(user_id: str) -> str:
        seen_users.append(user_id)
        return API_KEY

    result = _run(_adapter(recorder, key_provider=key_provider))

    assert result.text == "毎日勉強する。"
    assert seen_users == ["learner-1"]
    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == (
        f"{GEMINI_API_BASE_URL}/models/{MODEL}:generateContent"
    )
    assert request.url.params == httpx.QueryParams()  # key never in the URL
    assert request.headers["x-goog-api-key"] == API_KEY
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"]
    assert "勉強" in body["contents"][0]["parts"][0]["text"]
    assert body["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseSchema": GEMINI_RESPONSE_SCHEMA,
    }


def test_previous_sentence_is_included_in_prompt() -> None:
    recorder = Recorder([_ok(_sentence("今日"))])
    previous = GeneratedSentenceSchema.model_validate(_sentence("毎日"))

    _run(_adapter(recorder), previous)

    prompt = json.loads(recorder.requests[0].content)["contents"][0]["parts"][0]["text"]
    assert "毎日勉強する。" in prompt


def test_missing_key_sends_no_request() -> None:
    recorder = Recorder([])

    def no_key(_user_id: str) -> str:
        raise MissingGeminiKeyError()

    with pytest.raises(MissingGeminiKeyError):
        _run(_adapter(recorder, key_provider=no_key))
    assert recorder.requests == []


def test_key_provider_called_once_per_request_across_retries() -> None:
    calls: list[str] = []
    recorder = Recorder([httpx.Response(500), _ok(_sentence())])

    def key_provider(user_id: str) -> str:
        calls.append(user_id)
        return API_KEY

    _run(_adapter(recorder, key_provider=key_provider))

    assert calls == ["learner-1"]
    assert len(recorder.requests) == 2


@pytest.mark.parametrize(
    "discarded",
    [
        httpx.Response(200, json={"candidates": []}),
        httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
        _ok(_sentence(), finish="MAX_TOKENS"),
        _ok("{not json"),
        _ok({"text": "x", "tokens": []}),
        _ok(_sentence(with_target=False)),
        httpx.Response(200, content=b"<html>"),
    ],
    ids=["no-candidates", "blocked", "finish-reason", "bad-json", "schema",
         "no-target", "non-json-body"],
)
def test_discarded_output_is_retried(discarded: httpx.Response) -> None:
    recorder = Recorder([discarded, _ok(_sentence())])

    result = _run(_adapter(recorder))

    assert result.tokens[1].is_target
    assert len(recorder.requests) == 2


def test_more_than_fifty_tokens_is_discarded() -> None:
    long_sentence = _sentence()
    long_sentence["tokens"] = long_sentence["tokens"] * 26  # 52 tokens
    recorder = Recorder([_ok(long_sentence), _ok(_sentence())])

    result = _run(_adapter(recorder))

    assert len(result.tokens) == 2
    assert len(recorder.requests) == 2


def test_sentence_identical_to_previous_after_trim_is_discarded() -> None:
    previous = GeneratedSentenceSchema.model_validate(_sentence("毎日"))
    padded = _sentence("毎日")
    padded["text"] = " \u3000" + padded["text"] + "\n"
    recorder = Recorder([_ok(padded), _ok(_sentence("今日"))])

    result = _run(_adapter(recorder), previous)

    assert result.text == "今日勉強する。"
    assert len(recorder.requests) == 2


def test_sentence_differing_by_one_character_is_accepted() -> None:
    previous = GeneratedSentenceSchema.model_validate(_sentence("毎日"))
    candidate = _sentence("毎日")
    candidate["text"] = "毎日勉強する！"  # same tokens, last character differs
    recorder = Recorder([_ok(candidate)])

    result = _run(_adapter(recorder), previous)

    assert result.text == "毎日勉強する！"
    assert len(recorder.requests) == 1


def test_any_sentence_is_accepted_when_there_is_no_previous() -> None:
    recorder = Recorder([_ok(_sentence("毎日"))])

    result = _run(_adapter(recorder), None)

    assert result.text == "毎日勉強する。"
    assert len(recorder.requests) == 1


def test_system_instruction_asks_for_korean_meanings() -> None:
    assert "Korean" in SYSTEM_INSTRUCTION
    assert "English" not in SYSTEM_INSTRUCTION


def test_budget_exhausted_after_two_requests_raises_generation_error() -> None:
    recorder = Recorder([_ok("bad"), httpx.Response(503)])

    with pytest.raises(GenerationError) as exc_info:
        _run(_adapter(recorder))

    assert len(recorder.requests) == 2
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__


@pytest.mark.parametrize(
    "rejection",
    [
        httpx.Response(429),
        _rpc_error(429, "RESOURCE_EXHAUSTED"),
        _rpc_error(400, "RESOURCE_EXHAUSTED"),
    ],
)
def test_quota_rejection_stops_without_retry(rejection: httpx.Response) -> None:
    recorder = Recorder([rejection, _ok(_sentence())])

    with pytest.raises(QuotaExceededError):
        _run(_adapter(recorder))
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    "rejection",
    [
        _rpc_error(400, "INVALID_ARGUMENT", "API_KEY_INVALID"),
        _rpc_error(401, "UNAUTHENTICATED"),
        _rpc_error(403, "PERMISSION_DENIED"),
    ],
)
def test_invalid_key_rejection_stops_without_retry(rejection: httpx.Response) -> None:
    recorder = Recorder([httpx.Response(500), rejection, _ok(_sentence())])

    with pytest.raises(InvalidGeminiKeyError) as exc_info:
        _run(_adapter(recorder))
    assert len(recorder.requests) == 2
    assert API_KEY not in str(exc_info.value)


def test_attempt_timeout_raises_generation_timeout_and_ends_request() -> None:
    requests: list[httpx.Request] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await asyncio.sleep(1)
        return _ok(_sentence())

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    adapter = GeminiAdapter(
        lambda _u: API_KEY, client, model=MODEL, time_budget_seconds=0.05
    )

    with pytest.raises(GenerationTimeout) as exc_info:
        asyncio.run(adapter.generate("learner-1", TARGET, None))
    assert len(requests) == 1
    assert exc_info.value.__suppress_context__


def test_key_never_appears_in_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    outcomes = [
        [_rpc_error(429, "RESOURCE_EXHAUSTED")],
        [_rpc_error(400, "INVALID_ARGUMENT", "API_KEY_INVALID")],
        [httpx.ConnectError(f"boom {API_KEY}")] * 2,
    ]
    for responses in outcomes:
        with pytest.raises(Exception) as exc_info:
            _run(_adapter(Recorder(responses)))
        assert API_KEY not in str(exc_info.value)
        assert API_KEY not in repr(exc_info.value.__context__)
    assert API_KEY not in caplog.text


def test_invalid_model_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        GeminiAdapter(lambda _u: API_KEY, httpx.AsyncClient(), model="../x?key=1")


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (429, None, GeminiFailure.QUOTA),
        (400, b'{"error": {"status": "RESOURCE_EXHAUSTED"}}', GeminiFailure.QUOTA),
        (
            400,
            b'{"error": {"status": "INVALID_ARGUMENT", "details": '
            b'[{"reason": "API_KEY_INVALID"}]}}',
            GeminiFailure.INVALID_KEY,
        ),
        (401, None, GeminiFailure.INVALID_KEY),
        (403, b'{"error": {"status": "PERMISSION_DENIED"}}', GeminiFailure.INVALID_KEY),
        (400, b'{"error": {"status": "INVALID_ARGUMENT"}}', GeminiFailure.TRANSIENT),
        (404, None, GeminiFailure.TRANSIENT),
        (500, b"<html>", GeminiFailure.TRANSIENT),
        (503, b'[{"error": {"status": "UNAVAILABLE"}}]', GeminiFailure.TRANSIENT),
    ],
)
def test_classify_generation_failure(
    status: int, body: bytes | None, expected: GeminiFailure
) -> None:
    assert classify_generation_failure(status, body) is expected
