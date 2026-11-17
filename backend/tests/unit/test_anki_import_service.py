"""Unit tests for the bulk word methods and the Anki import service (task 17.15).

Covers ``WordService.existing_word_forms`` / ``add_words_bulk``, the pure
``plan_import`` rules (Req 2.23-2.29), and ``AnkiImportService`` preview,
confirm, and failure handling (Req 2.4-2.9, 2.21, 2.22, 2.26, 2.30-2.34).
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - register all models on Base.metadata
from app.api.upload import ReceivedFile
from app.db.session import Base
from app.models.review_record import ReviewRecord
from app.models.user import User
from app.models.word_entry import WordEntry
from app.schemas.word import WordInput
from app.schemas.word_import import ConfirmImportIn, FieldMappingOut
from app.services.anki_import.errors import (
    ImportFailedError,
    InvalidMappingError,
    InvalidNoteTypeError,
    PreviewExpiredError,
    UnsupportedExtensionError,
)
from app.services.anki_import.import_service import (
    AnkiImportService,
    SkippedNote,
    SkipReason,
    plan_import,
)
from app.services.anki_import.mapping import FieldMapping
from app.services.anki_import.preview_store import PreviewStore
from app.services.word_service import WordService
from tests.fixtures.anki_builders import (
    NoteSpec,
    NoteTypeSpec,
    build_anki21b_apkg,
    build_text_export,
)

TODAY = date(2024, 5, 1)
WRM = FieldMapping(word=0, reading=1, meaning=2)
WM = FieldMapping(word=0, reading=None, meaning=1)


# --------------------------------------------------------------- fixtures


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_user(db: Session, email: str) -> User:
    user = User(email=email, oidc_issuer="https://accounts.google.com", oidc_subject=email)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def user(db: Session) -> User:
    return _make_user(db, "learner@example.com")


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2024, 5, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def store(clock: FakeClock, tmp_path: Path) -> PreviewStore:
    return PreviewStore(clock=clock, temp_root=tmp_path)


def _service(db: Session, store: PreviewStore, words: WordService | None = None) -> AnkiImportService:
    return AnkiImportService(db, store, words or WordService(db), today=lambda: TODAY)


def _received(tmp_path: Path, name: str, data: bytes) -> ReceivedFile:
    path = tmp_path / f"upload-{name}.bin"
    path.write_bytes(data)
    return ReceivedFile(filename=name, path=path, size=len(data))


def _text_preview(service: AnkiImportService, user: User, tmp_path: Path, rows, headers=()):
    data = build_text_export(rows, headers)
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    return service.create_preview(user.id, _received(tmp_path, "deck.txt", data), workdir)


def _rows(db: Session, user_id: str) -> list[tuple]:
    db.expire_all()
    return sorted(
        db.execute(
            select(
                WordEntry.id,
                WordEntry.word_form,
                WordEntry.reading,
                WordEntry.meaning,
                WordEntry.next_review_date,
            ).where(WordEntry.user_id == user_id)
        ).all()
    )


def _seed(db: Session, user_id: str, *forms: str) -> None:
    for form in forms:
        WordService(db).add_word(
            user_id, WordInput(word_form=form, reading="よみ", meaning="seed"), today=TODAY
        )


def _confirm_body(word=0, reading=1, meaning=2, note_type_id=None) -> ConfirmImportIn:
    return ConfirmImportIn(
        note_type_id=note_type_id,
        mapping=FieldMappingOut(word=word, reading=reading, meaning=meaning),
    )


# ------------------------------------------------------ WordService bulk


def test_existing_word_forms_returns_only_the_users_forms(db: Session, user: User) -> None:
    other = _make_user(db, "other@example.com")
    _seed(db, user.id, "猫", "犬")
    _seed(db, other.id, "鳥")
    assert WordService(db).existing_word_forms(user.id) == {"猫", "犬"}
    assert WordService(db).existing_word_forms("nobody") == set()


def test_add_words_bulk_inserts_due_today_with_default_state_in_chunks(
    db: Session, user: User
) -> None:
    inputs = [WordInput(word_form=f"語{i}", reading=f"ご{i}", meaning=f"m{i}") for i in range(5)]
    assert WordService(db).add_words_bulk(user.id, inputs, TODAY, chunk_size=2) == 5
    db.commit()
    entries = db.execute(select(WordEntry).where(WordEntry.user_id == user.id)).scalars().all()
    assert sorted(e.word_form for e in entries) == [f"語{i}" for i in range(5)]
    assert len({e.id for e in entries}) == 5
    for e in entries:
        assert e.next_review_date == TODAY
        assert (e.ease_factor, e.interval_days, e.repetitions) == (2.5, 0, 0)
        assert e.reading == "ご" + e.word_form[1:]
        assert e.created_at is not None
    assert db.execute(select(ReviewRecord)).first() is None


def test_add_words_bulk_does_not_commit(db: Session, user: User) -> None:
    inputs = [WordInput(word_form="猫", reading="ねこ", meaning="cat")]
    WordService(db).add_words_bulk(user.id, inputs, TODAY)
    db.rollback()
    assert _rows(db, user.id) == []


def test_add_words_bulk_with_no_inputs_inserts_nothing(db: Session, user: User) -> None:
    assert WordService(db).add_words_bulk(user.id, [], TODAY) == 0
    with pytest.raises(ValueError):
        WordService(db).add_words_bulk(user.id, [], TODAY, chunk_size=0)


def test_add_word_still_commits_and_sets_default_state(db: Session, user: User) -> None:
    entry = WordService(db).add_word(
        user.id, WordInput(word_form="猫", reading="ねこ", meaning="cat"), today=TODAY
    )
    db.rollback()
    assert [r[1] for r in _rows(db, user.id)] == ["猫"]
    assert (entry.next_review_date, entry.ease_factor, entry.interval_days, entry.repetitions) == (
        TODAY,
        2.5,
        0,
        0,
    )


# ------------------------------------------------------------ plan_import


def test_plan_import_applies_the_first_matching_rule_in_file_order() -> None:
    notes = [
        ("猫", "ねこ", "cat"),  # created
        ("", "x", "no word"),  # missing (empty word)
        ("犬", "いぬ", "  <br> "),  # missing (meaning cleans to empty)
        ("", "", "m" * 501),  # missing wins over invalid length
        ("長" * 51, "", "long word"),  # invalid length (word)
        ("語", "よ" * 101, "long reading"),  # invalid length (reading)
        ("語2", "", "m" * 501),  # invalid length (meaning)
        ("既存", "きそん", "existing"),  # duplicate of an existing form
        ("猫", "ねこ", "cat again"),  # duplicate of a form planned earlier
        ("ア" * 50, "よ" * 100, "m" * 500),  # exactly at the limits: created
    ]
    plan = plan_import(notes, WRM, {"既存"})
    assert [w.word_form for w in plan.to_create] == ["猫", "ア" * 50]
    assert plan.skipped == [
        SkippedNote(None, SkipReason.MISSING_FIELD),
        SkippedNote("犬", SkipReason.MISSING_FIELD),
        SkippedNote(None, SkipReason.MISSING_FIELD),
        SkippedNote("長" * 51, SkipReason.INVALID_LENGTH),
        SkippedNote("語", SkipReason.INVALID_LENGTH),
        SkippedNote("語2", SkipReason.INVALID_LENGTH),
        SkippedNote("既存", SkipReason.DUPLICATE),
        SkippedNote("猫", SkipReason.DUPLICATE),
    ]


def test_plan_import_cleans_fields_and_resolves_furigana_readings() -> None:
    notes = [
        ("<b>食[た]べる</b>", "", "to eat&nbsp;[sound:a.mp3]"),  # 2.25(b)
        ("漢字[かんじ]", "カンジ", "kanji"),  # 2.25(a) wins, word stripped (2.24)
        ("ねこ", "   ", "cat"),  # 2.25(c): reading cleans to empty
    ]
    plan = plan_import(notes, WRM, set())
    assert [(w.word_form, w.reading, w.meaning) for w in plan.to_create] == [
        ("食べる", "たべる", "to eat"),
        ("漢字", "カンジ", "kanji"),
        ("ねこ", "ねこ", "cat"),
    ]


def test_plan_import_dedupes_on_the_resolved_word_form() -> None:
    plan = plan_import([("猫[ねこ]", "cat"), ("猫", "cat 2")], WM, set())
    assert [w.word_form for w in plan.to_create] == ["猫"]
    assert plan.skipped == [SkippedNote("猫", SkipReason.DUPLICATE)]


# -------------------------------------------------------- preview building


def test_create_text_preview_builds_fields_mapping_and_cleaned_samples(
    db: Session, user: User, store: PreviewStore, clock: FakeClock, tmp_path: Path
) -> None:
    rows = [[f"<i>語{i}</i>", f"ご{i}", f"m{i}<br>x"] for i in range(7)]
    preview = _text_preview(
        _service(db, store), user, tmp_path, rows, headers=[("columns", "Word\tReading\tMeaning")]
    )
    assert preview.file_kind == "text"
    assert preview.note_types == [] and preview.selected_note_type_id is None
    assert [(f.index, f.name) for f in preview.fields] == [
        (0, "Word"),
        (1, "Reading"),
        (2, "Meaning"),
    ]
    assert preview.suggested_mapping.model_dump() == {"word": 0, "reading": 1, "meaning": 2}
    assert preview.note_count == 7
    assert preview.samples == [[f"語{i}", f"ご{i}", f"m{i} x"] for i in range(5)]
    assert preview.expires_at == clock.now + timedelta(minutes=30)
    assert _rows(db, user.id) == []  # Req 2.4: nothing written by a preview


def test_create_preview_rejects_unsupported_extension_without_storing(
    db: Session, user: User, store: PreviewStore, tmp_path: Path
) -> None:
    with pytest.raises(UnsupportedExtensionError):
        _service(db, store).create_preview(
            user.id, _received(tmp_path, "deck.zip", b"x"), tmp_path
        )


def _two_type_apkg() -> bytes:
    basic = NoteTypeSpec(id=10, name="Basic", field_names=("Front", "Back"))
    vocab = NoteTypeSpec(id=20, name="Vocab", field_names=("Expression", "Kana", "Meaning", "Extra"))
    notes = [
        NoteSpec(id=5, mid=20, values=("犬", "いぬ", "dog", "")),
        NoteSpec(id=1, mid=20, values=("猫", "ねこ", "cat", "")),
        NoteSpec(id=3, mid=20, values=("鳥", "とり", "bird", "")),
        NoteSpec(id=2, mid=10, values=("水", "water")),
    ]
    return build_anki21b_apkg([basic, vocab], notes)


def test_apkg_preview_defaults_to_the_largest_type_and_can_switch(
    db: Session, user: User, store: PreviewStore, tmp_path: Path
) -> None:
    service = _service(db, store)
    workdir = tmp_path / "work"
    workdir.mkdir()
    preview = service.create_preview(
        user.id, _received(tmp_path, "deck.APKG", _two_type_apkg()), workdir
    )
    assert preview.file_kind == "apkg"
    assert sorted((t.id, t.name, t.note_count) for t in preview.note_types) == [
        ("10", "Basic", 1),
        ("20", "Vocab", 3),
    ]
    assert preview.selected_note_type_id == "20"
    assert [f.name for f in preview.fields] == ["Expression", "Kana", "Meaning", "Extra"]
    assert preview.suggested_mapping.model_dump() == {"word": 0, "reading": 1, "meaning": 2}
    assert [s[0] for s in preview.samples] == ["猫", "鳥", "犬"]  # ascending note id

    switched = service.get_preview(user.id, preview.preview_id, "10")
    assert switched.selected_note_type_id == "10"
    assert [f.name for f in switched.fields] == ["Front", "Back"]
    assert switched.suggested_mapping.model_dump() == {"word": 0, "reading": None, "meaning": 1}
    assert switched.note_count == 1 and switched.samples == [["水", "water"]]

    with pytest.raises(InvalidNoteTypeError):
        service.get_preview(user.id, preview.preview_id, "999")

    # The confirm uses the selected type (Basic) when the body names none (Req 2.9).
    summary = service.confirm(user.id, preview.preview_id, _confirm_body(0, None, 1))
    assert summary.created == 1
    assert [r[1:4] for r in _rows(db, user.id)] == [("水", "水", "water")]


# ----------------------------------------------------------------- confirm


def test_confirm_imports_and_reports_the_summary(
    db: Session, user: User, store: PreviewStore, tmp_path: Path
) -> None:
    other = _make_user(db, "other@example.com")
    _seed(db, user.id, "既存")
    _seed(db, other.id, "猫")  # another account's word is not a duplicate (Req 2.37)
    rows = (
        [["猫", "ねこ", "cat"], ["既存", "", "dup"], ["", "", "no word"]]
        + [[f"重{i}", "", "x" * 501] for i in range(25)]
        + [["犬[いぬ]", "", "dog"]]
    )
    service = _service(db, store)
    preview = _text_preview(service, user, tmp_path, rows)
    summary = service.confirm(user.id, preview.preview_id, _confirm_body())

    assert (
        summary.created,
        summary.skipped_missing_field,
        summary.skipped_duplicate,
        summary.skipped_invalid_length,
    ) == (2, 1, 1, 25)
    assert len(summary.skipped) == 20
    assert [(s.word_form, s.reason) for s in summary.skipped[:3]] == [
        ("既存", "duplicate"),
        (None, "missing_field"),
        ("重0", "invalid_length"),
    ]
    assert summary.skipped[-1].word_form == "重17"

    stored = {r[1]: r[2:] for r in _rows(db, user.id)}
    assert stored == {
        "既存": ("よみ", "seed", TODAY),
        "猫": ("ねこ", "cat", TODAY),
        "犬": ("いぬ", "dog", TODAY),
    }
    assert [r[1] for r in _rows(db, other.id)] == ["猫"]
    # The preview is consumed: a repeat confirm is rejected (Req 2.36).
    with pytest.raises(PreviewExpiredError):
        service.confirm(user.id, preview.preview_id, _confirm_body())


def test_confirm_rejects_an_invalid_mapping_and_keeps_the_preview(
    db: Session, user: User, store: PreviewStore, tmp_path: Path
) -> None:
    service = _service(db, store)
    preview = _text_preview(service, user, tmp_path, [["猫", "ねこ", "cat"]])
    with pytest.raises(InvalidMappingError) as excinfo:
        service.confirm(user.id, preview.preview_id, _confirm_body(word=1, reading=1, meaning=None))
    assert dict(excinfo.value.errors) == {
        "word": "duplicate",
        "reading": "duplicate",
        "meaning": "missing",
    }
    assert _rows(db, user.id) == []
    assert service.confirm(user.id, preview.preview_id, _confirm_body()).created == 1


def test_confirm_rejects_an_unlisted_note_type_for_apkg(
    db: Session, user: User, store: PreviewStore, tmp_path: Path
) -> None:
    service = _service(db, store)
    workdir = tmp_path / "work"
    workdir.mkdir()
    preview = service.create_preview(
        user.id, _received(tmp_path, "deck.apkg", _two_type_apkg()), workdir
    )
    with pytest.raises(InvalidNoteTypeError):
        service.confirm(user.id, preview.preview_id, _confirm_body(note_type_id="999"))
    assert service.get_preview(user.id, preview.preview_id).preview_id == preview.preview_id


def test_confirm_rejects_expired_and_foreign_previews(
    db: Session, user: User, store: PreviewStore, clock: FakeClock, tmp_path: Path
) -> None:
    other = _make_user(db, "other@example.com")
    service = _service(db, store)
    preview = _text_preview(service, user, tmp_path, [["猫", "ねこ", "cat"]])
    with pytest.raises(PreviewExpiredError):
        service.confirm(other.id, preview.preview_id, _confirm_body())
    clock.now += timedelta(minutes=30)
    with pytest.raises(PreviewExpiredError):
        service.confirm(user.id, preview.preview_id, _confirm_body())
    assert _rows(db, user.id) == [] and _rows(db, other.id) == []


def test_cancel_discards_the_preview(
    db: Session, user: User, store: PreviewStore, tmp_path: Path
) -> None:
    service = _service(db, store)
    preview = _text_preview(service, user, tmp_path, [["猫", "ねこ", "cat"]])
    service.cancel(user.id, preview.preview_id)
    service.cancel(user.id, preview.preview_id)  # idempotent
    with pytest.raises(PreviewExpiredError):
        service.confirm(user.id, preview.preview_id, _confirm_body())


# ------------------------------------------------------- failure atomicity


class FailAfterInsertWordService(WordService):
    """Inserts the first ``k`` planned rows one per chunk, then fails."""

    def __init__(self, db: Session, k: int) -> None:
        super().__init__(db)
        self.k = k

    def add_words_bulk(self, user_id, inputs, today, chunk_size=1000):  # type: ignore[override]
        super().add_words_bulk(user_id, list(inputs)[: self.k], today, chunk_size=1)
        raise RuntimeError("database write failed")


class StaleSnapshotWordService(WordService):
    """Skips the dedupe read so a real unique-constraint violation happens."""

    def existing_word_forms(self, user_id: str) -> set[str]:
        return set()


@pytest.mark.parametrize("k", [0, 1, 3])
def test_failed_write_rolls_back_every_row_and_removes_the_preview(
    db: Session, user: User, store: PreviewStore, tmp_path: Path, k: int
) -> None:
    _seed(db, user.id, "既存")
    before = _rows(db, user.id)
    words = FailAfterInsertWordService(db, k)
    service = _service(db, store, words)
    preview = _text_preview(service, user, tmp_path, [["猫", "", "cat"], ["犬", "", "dog"], ["鳥", "", "bird"]])

    with pytest.raises(ImportFailedError) as excinfo:
        service.confirm(user.id, preview.preview_id, _confirm_body(0, None, 2))
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__

    assert _rows(db, user.id) == before
    with pytest.raises(PreviewExpiredError):
        store.get_pending(preview.preview_id, user.id)


def test_unique_constraint_violation_rolls_back_the_whole_import(
    db: Session, user: User, store: PreviewStore, tmp_path: Path
) -> None:
    _seed(db, user.id, "既存")
    before = _rows(db, user.id)
    service = _service(db, store, StaleSnapshotWordService(db))
    preview = _text_preview(service, user, tmp_path, [["猫", "", "cat"], ["既存", "", "dup"]])
    with pytest.raises(ImportFailedError):
        service.confirm(user.id, preview.preview_id, _confirm_body(0, None, 2))
    assert _rows(db, user.id) == before


def test_failed_commit_rolls_back_every_row(
    db: Session, user: User, store: PreviewStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _rows(db, user.id)
    service = _service(db, store)
    preview = _text_preview(service, user, tmp_path, [["猫", "", "cat"], ["犬", "", "dog"]])

    def failing_commit() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(ImportFailedError):
        service.confirm(user.id, preview.preview_id, _confirm_body(0, None, 2))
    monkeypatch.undo()
    assert _rows(db, user.id) == before
