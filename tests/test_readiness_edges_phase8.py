from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import prewarm, reminder
from services.validators import db as db_validator
from tests.test_prewarm_cache_phase8 import FakeBot as PrewarmBot
from tests.test_prewarm_cache_phase8 import FakeCatalog, configure_prewarm
from tests.test_reminder_phase8 import ApiError, Connection, FakeBot, install_db


def test_validator_legacy_self_path_exclusions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    exact = tmp_path / "services" / "validators"
    exact.mkdir(parents=True)
    (exact / "data.db").write_bytes(b"ignored")
    monkeypatch.setattr(db_validator, "PROJECT_ROOT", exact)
    db_validator.validate_no_real_db(strict=True)

    partial = tmp_path / "services" / "validators-shadow"
    partial.mkdir(parents=True)
    (partial / "data.db").write_bytes(b"ignored")
    monkeypatch.setattr(db_validator, "PROJECT_ROOT", partial)
    db_validator.validate_no_real_db(strict=True)


def test_real_prewarm_marker_location() -> None:
    marker = prewarm._marker_path()
    assert marker.name == "audio.done"
    assert marker.parent.name == "prewarm"
    assert marker.parent.is_dir()


@pytest.mark.asyncio
async def test_voice_without_file_id_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    saved, marked = configure_prewarm(monkeypatch)
    voice = tmp_path / "voice.ogg"
    voice.write_bytes(b"voice")
    FakeCatalog.demo = [voice]

    await prewarm.prewarm_audio_cache(PrewarmBot(voice_id=None))

    assert saved == []
    assert marked == []


@pytest.mark.asyncio
async def test_deadline_transport_failure_does_not_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    install_db(monkeypatch, Connection([{"user_id": 30}]))
    monkeypatch.setattr(reminder, "utc_now", lambda: now)
    monkeypatch.setattr(reminder, "TelegramAPIError", ApiError)
    monkeypatch.setattr(
        reminder,
        "first_ts_for",
        lambda _uid, _event: (now - timedelta(hours=25)).isoformat(),
    )
    monkeypatch.setattr(
        reminder,
        "step_done",
        lambda _uid, step: step == "reminded_1",
    )
    marks: list[tuple[int, str]] = []
    monkeypatch.setattr(reminder, "mark_step", lambda uid, step: marks.append((uid, step)))
    bot = FakeBot(fail_users={30})

    await reminder._funnel_reminder_once(bot)

    assert [item[0] for item in bot.messages] == [30]
    assert marks == []
