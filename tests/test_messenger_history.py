from services.schema import init_db
from services.messenger.audio_progress import get_audio_item_by_anchor, get_progress_snapshot, record_audio_delivery
from services.messenger.audio_access import issue_or_reuse_audio_access_token, register_audio_access
from services.messenger.timeline import get_recent_audio_timeline


def test_audio_timeline_records_issue_and_non_confirming_access(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db()
    item = get_audio_item_by_anchor(1)
    assert item is not None
    token = issue_or_reuse_audio_access_token(42, item=item, platform="vk")
    register_audio_access(token)

    events = get_recent_audio_timeline(42, sequence_key="full_series", limit=5)
    names = [event.event_type for event in events]
    assert "accessed" in names
    assert "access_confirmed" not in names
    assert "issued_pending" in names or "reused_pending" in names

    snapshot = get_progress_snapshot(42)
    assert snapshot.last_anchor is None
    assert snapshot.pending_item is not None
    assert snapshot.pending_item.anchor == 1


def test_audio_timeline_records_telegram_confirmation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db()
    item = get_audio_item_by_anchor(1)
    assert item is not None
    record_audio_delivery(7, item=item, platform="telegram")
    events = get_recent_audio_timeline(7, sequence_key="full_series", limit=5)
    assert events
    assert events[0].event_type == "confirmed_delivery"
