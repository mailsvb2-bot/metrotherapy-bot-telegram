import asyncio
from dataclasses import dataclass
from pathlib import Path

from services.messenger.text_ui import handle_incoming_text
from services.messenger.preferences import get_channel_snapshot
from services.messenger.menu_contract import MAIN_MENU_ACTIONS
from services.messenger.entrypoints import register_user_entry
from services.messenger.outbound import SenderRegistry
from services.mood import create_session
from services.mood_text_flow import complete_pre_score_and_send
from services.schema import init_db


def setup_module(module):
    init_db()


def test_platform_command_updates_preference():
    canonical_user_id, replies = handle_incoming_text(901001, platform='vk', external_user_id='901001', text='/platform max')
    assert canonical_user_id == 901001
    assert replies
    snapshot = get_channel_snapshot(901001)
    assert snapshot['preferred_platform'] == 'max'


def test_share_command_returns_targets_text():
    canonical_user_id, replies = handle_incoming_text(901002, platform='max', external_user_id='901002', text='share')
    assert canonical_user_id == 901002
    assert replies
    assert 'Поделиться' in replies[0].text or 'пока не настроены' in replies[0].text


def test_continue_command_returns_next_audio_action():
    canonical_user_id, replies = handle_incoming_text(901003, platform='telegram', external_user_id='901003', text='continue')
    assert canonical_user_id == 901003
    assert replies[0].kind == 'next_audio'


def test_vk_and_max_accept_all_canonical_main_menu_titles():
    expected_fragments = {
        'demo': 'Бесплатная практика',
        'full': 'Полный маршрут',
        'pay': 'Оплата доступа',
        'gift': 'Подарить Метротерапию',
        'progress': 'прогресс',
        'settings': 'Настройки канала',
        'share': 'Поделиться',
        'weather': 'Погода',
    }

    for platform in ('vk', 'max'):
        for offset, action in enumerate(MAIN_MENU_ACTIONS, start=1):
            canonical_user_id = 902000 + offset + (100 if platform == 'max' else 0)
            _, replies = handle_incoming_text(
                canonical_user_id,
                platform=platform,
                external_user_id=str(canonical_user_id),
                text=action.title,
            )
            assert replies, (platform, action.command)
            assert expected_fragments[action.command].casefold() in replies[0].text.casefold(), (platform, action.command, replies[0].text)


def test_vk_and_max_accept_context_buttons_by_title():
    for platform in ('vk', 'max'):
        _, replies = handle_incoming_text(903001, platform=platform, external_user_id='903001', text='🎧 Получить аудио')
        assert replies[0].kind == 'text'
        assert 'Бесплатная практика' in replies[0].text or replies[0].kind == 'next_audio'

        _, replies = handle_incoming_text(903002, platform=platform, external_user_id='903002', text='✅ Прослушал')
        assert replies
        assert replies[0].kind in {'text', 'auto_post_score'}


@dataclass(frozen=True)
class FakeAnchoredAudio:
    anchor: int
    path: Path
    clean_title: str


class FailingMaxSender:
    def __init__(self) -> None:
        self.audio_attempts: list[tuple[str, Path, str | None]] = []
        self.text_messages: list[tuple[str, str, dict]] = []

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs):
        self.audio_attempts.append((external_user_id, file_path, caption))
        raise RuntimeError('simulated MAX native audio failure')

    async def send_text(self, external_user_id: str, text: str, **kwargs):
        self.text_messages.append((external_user_id, text, kwargs))
        return {'ok': True}


def test_max_pre_score_audio_uses_link_fallback_when_native_send_fails(tmp_path, monkeypatch):
    audio_path = tmp_path / '001 test audio.ogg'
    audio_path.write_bytes(b'fake-audio')

    user_id = 987654
    external_user_id = 'max-user-987654'

    register_user_entry(
        user_id,
        platform='max',
        external_user_id=external_user_id,
        username='max_test_user',
        first_name='Max',
    )
    create_session(
        user_id,
        kind='work',
        source='settings',
        day='2026-05-14',
        slot='morning',
        anchor_id=1,
    )

    import services.mood_text_flow as flow

    monkeypatch.setattr(flow, 'get_by_anchor', lambda anchor: FakeAnchoredAudio(1, audio_path, 'test audio'))
    monkeypatch.setattr(flow, 'ensure_max_opus_file', lambda path: path)
    monkeypatch.setattr(flow, 'issue_or_reuse_audio_access_token', lambda user_id, *, item, platform: 'tok_max_fallback')
    monkeypatch.setattr(flow, 'build_audio_access_url', lambda token: f'https://example.test/audio/{token}')

    sender = FailingMaxSender()
    result = asyncio.run(
        complete_pre_score_and_send(
            user_id,
            platform='max',
            score=-4,
            senders=SenderRegistry(max=sender),
        )
    )

    assert result.ok is True
    assert result.transport == 'max_link'
    assert sender.audio_attempts, 'MAX native audio must be attempted first'
    assert sender.text_messages, 'MAX fallback link must be sent after native failure'
    assert sender.text_messages[0][0] == external_user_id
    assert 'https://example.test/audio/tok_max_fallback' in sender.text_messages[0][1]
    assert 'native-отправка MAX сейчас не прошла' in sender.text_messages[0][1]

def test_new_pre_score_session_wins_over_old_pending_post_score():
    from services.db import db
    from services.migrations import apply_all_migrations
    from services.messenger.text_ui import handle_incoming_text
    from services.mood import create_session, set_pre, mark_audio_sent, get_session
    from services.messenger.preferences import record_channel_identity

    user_id = 991430

    with db() as conn:
        apply_all_migrations(conn)
        conn.execute("DELETE FROM mood_sessions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM messenger_channel_identities WHERE user_id=?", (user_id,))

    record_channel_identity(user_id, "max", "mx-991430")

    old_session_id = create_session(
        user_id,
        kind="work",
        source="settings",
        day="2026-05-15",
        slot="morning",
        scheduled_at=None,
        anchor_id=1,
    )
    assert set_pre(old_session_id, -6)
    mark_audio_sent(old_session_id)

    new_session_id = create_session(
        user_id,
        kind="work",
        source="settings",
        day="2026-05-15",
        slot="morning",
        scheduled_at=None,
        anchor_id=2,
    )

    canonical_user_id, replies = handle_incoming_text(
        user_id,
        platform="max",
        external_user_id="mx-991430",
        text="-5",
    )

    assert canonical_user_id == user_id
    assert replies[0].kind == "auto_pre_score"

    old_session = get_session(old_session_id)
    new_session = get_session(new_session_id)

    assert old_session is not None
    assert new_session is not None
    assert old_session.post_score is None
    assert new_session.pre_score == -5
