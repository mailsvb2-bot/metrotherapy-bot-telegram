import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from services.messenger.text_ui import handle_incoming_text
from services.messenger.preferences import get_channel_snapshot
from services.messenger.menu_contract import MAIN_MENU_ACTIONS
from services.messenger.entrypoints import register_user_entry
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery
from services.mood import create_session
from services.mood_text_flow import complete_pre_score_and_send, NATIVE_AUDIO_REQUIRED_MESSAGE
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
        'pay': 'Тарифы Метротерапии',
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


def test_max_pre_score_audio_requires_native_audio_and_never_sends_link(tmp_path, monkeypatch):
    audio_path = tmp_path / '001 test audio.ogg'
    audio_path.write_bytes(b'fake-audio')

    import services.mood_text_flow as flow
    import services.messenger.audio_delivery as delivery

    monkeypatch.setattr(flow, '_audio_for_anchor', lambda anchor: FakeAnchoredAudio(anchor, audio_path, 'test audio'))
    monkeypatch.setattr(delivery, '_audio_for_anchor', lambda anchor: FakeAnchoredAudio(anchor, audio_path, 'test audio'))
    monkeypatch.setattr(flow, 'create_session', lambda *args, **kwargs: 9001)
    monkeypatch.setattr(flow, 'mark_pre_score', lambda *args, **kwargs: None)
    monkeypatch.setattr(flow, 'mark_audio_sent', lambda *args, **kwargs: None)
    monkeypatch.setattr(delivery, 'mark_audio_sent', lambda *args, **kwargs: None)
    monkeypatch.setattr(flow, 'get_current_or_next_item', lambda user_id: type('Item', (), {'anchor': 1, 'title': 'test audio'})())
    monkeypatch.setattr(delivery, 'get_current_or_next_item', lambda user_id: type('Item', (), {'anchor': 1, 'title': 'test audio'})())

    sender = FailingMaxSender()
    registry = SenderRegistry(max=sender)

    async def run():
        return await complete_pre_score_and_send(77, platform='max', score=3, senders=registry)

    result = asyncio.run(run())

    assert not result.ok
    assert result.transport == 'max'
    assert NATIVE_AUDIO_REQUIRED_MESSAGE in result.message
    assert sender.audio_attempts
    assert sender.text_messages == []


def test_outbound_registry_rejects_link_fallback_for_max_audio(tmp_path):
    audio_path = tmp_path / '002 test audio.ogg'
    audio_path.write_bytes(b'fake-audio')
    registry = SenderRegistry()

    async def run():
        with pytest.raises(UnsupportedMessengerDelivery):
            await registry.send_audio('max', 'mx77', audio_path, caption='caption')

    asyncio.run(run())
