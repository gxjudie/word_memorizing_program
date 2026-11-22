"""LLM structured-output schema for generated example sentences.

The Sentence Generation Service asks the provider for an Example_Sentence as
JSON matching the schema below (enforced with the provider's structured-output
/ JSON mode) and then **re-validates it with Pydantic on the backend** — the
model output is never trusted. These schemas are the Pydantic equivalent of the
former Zod ``GeneratedSentenceSchema``: per-field constraints, a 1-50 token
list, a model validator requiring at least one target token, and a field
validator rejecting an empty surface-form reconstruction.

Requirements: 4.1 (target present), 4.4 (1-50 words), 4.6 (target emphasis
relies on at least one ``is_target`` token), 3.2, 5.1, 5.2, 5.4 (per-token
reading / meaning / kanji metadata consumed by the Sentence renderer and
Lookup_Panel).
"""

from pydantic import BaseModel, Field, field_validator, model_validator


class SentenceTokenSchema(BaseModel):
    """A single tokenized word within a generated sentence.

    ``surface`` is the word as written in the sentence and ``reading`` is its
    hiragana reading (both required and non-empty). ``meaning`` is an optional
    short gloss. ``is_target`` marks the token(s) that are the reviewed
    Target_Word, and ``contains_kanji`` flags tokens that include kanji so the
    renderer can offer furigana (Req 5.1, 5.2, 5.4).
    """

    surface: str = Field(min_length=1)
    reading: str = Field(min_length=1)
    meaning: str | None
    is_target: bool
    contains_kanji: bool


class GeneratedSentenceSchema(BaseModel):
    """A validated example sentence returned by the LLM.

    ``text`` is the full natural-Japanese sentence; ``tokens`` is its word-level
    tokenization constrained to 1-50 entries (Req 4.4). At least one token must
    be the target (Req 4.1 / 4.6), and the concatenated surface forms must not
    reconstruct to an empty string.
    """

    text: str = Field(min_length=1)
    # 1-50 words (Req 4.4)
    tokens: list[SentenceTokenSchema] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _must_contain_target(self) -> "GeneratedSentenceSchema":
        # target present (Req 4.1 / 4.6)
        if not any(t.is_target for t in self.tokens):
            raise ValueError("no target token")
        return self

    @field_validator("tokens")
    @classmethod
    def _reconstruction_sanity(
        cls, tokens: list[SentenceTokenSchema]
    ) -> list[SentenceTokenSchema]:
        joined = "".join(t.surface for t in tokens)
        if len("".join(joined.split())) == 0:
            raise ValueError("empty reconstruction")
        return tokens


# Gemini ``responseSchema`` (OpenAPI subset) mirroring GeneratedSentenceSchema.
# Sent as ``generationConfig.responseSchema`` with
# ``responseMimeType = "application/json"``. Gemini output is still
# re-validated with GeneratedSentenceSchema, so this schema only shapes the
# model's output. A unit test keeps the field names and required sets of the
# two schemas in sync.
GEMINI_RESPONSE_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "text": {"type": "STRING"},
        "tokens": {
            "type": "ARRAY",
            # Array length limits (1-50) are intentionally omitted here: Gemini's
            # structured-output engine rejects minItems/maxItems on this nested
            # array (HTTP 400 INVALID_ARGUMENT, "too many states for serving").
            # The 1-50 bound is enforced in Pydantic by GeneratedSentenceSchema,
            # which re-validates every Gemini response after the call.
            "items": {
                "type": "OBJECT",
                "properties": {
                    "surface": {"type": "STRING"},
                    "reading": {"type": "STRING"},
                    "meaning": {"type": "STRING", "nullable": True},
                    "is_target": {"type": "BOOLEAN"},
                    "contains_kanji": {"type": "BOOLEAN"},
                },
                "required": [
                    "surface",
                    "reading",
                    "meaning",
                    "is_target",
                    "contains_kanji",
                ],
                "propertyOrdering": [
                    "surface",
                    "reading",
                    "meaning",
                    "is_target",
                    "contains_kanji",
                ],
            },
        },
    },
    "required": ["text", "tokens"],
    "propertyOrdering": ["text", "tokens"],
}


# Domain-model aliases used by the services layer. Services depend on the
# names ``SentenceToken`` / ``GeneratedSentence`` so that the schema class
# names can evolve independently of their call sites.
SentenceToken = SentenceTokenSchema
GeneratedSentence = GeneratedSentenceSchema

__all__ = [
    "SentenceTokenSchema",
    "GeneratedSentenceSchema",
    "SentenceToken",
    "GeneratedSentence",
    "GEMINI_RESPONSE_SCHEMA",
]
