from __future__ import annotations

from importlib.util import find_spec

import pytest

if find_spec('aiogram') is None:
    pytestmark = pytest.mark.skip(reason='aiogram is not installed in this test environment')
else:
    from datetime import datetime, UTC

    from aiogram.types import CallbackQuery, Chat, Message, User

    from core.middlewares import QuickAckCallbackMiddleware, SoftRateLimitMiddleware


    def _make_callback(user_id: int, data: str = 'x') -> CallbackQuery:
        user = User(id=user_id, is_bot=False, first_name='Test')
        cb = CallbackQuery(id='1', from_user=user, chat_instance='chat', data=data)
        object.__setattr__(cb, "answer", _async_stub())
        return cb


    def _make_message(user_id: int, text: str = 'hello') -> Message:
        user = User(id=user_id, is_bot=False, first_name='Test')
        chat = Chat(id=user_id, type='private')
        msg = Message(message_id=1, date=datetime.now(UTC), chat=chat, from_user=user, text=text)
        object.__setattr__(msg, "answer", _async_stub())
        return msg


    def _async_stub():
        calls = []

        async def _inner(*args, **kwargs):
            calls.append((args, kwargs))
            return None

        _inner.calls = calls
        return _inner


    @pytest.mark.asyncio
    async def test_callback_and_message_are_limited_separately():
        mw = SoftRateLimitMiddleware(callback_interval_sec=1.0, message_interval_sec=1.0)
        cb = _make_callback(1, 'same')
        msg = _make_message(1, 'same')
        seen = []

        async def handler(event, data):
            seen.append(type(event).__name__)
            return 'ok'

        assert await mw(handler, cb, {}) == 'ok'
        assert await mw(handler, msg, {}) == 'ok'
        assert seen == ['CallbackQuery', 'Message']


    @pytest.mark.asyncio
    async def test_duplicate_callback_is_soft_blocked():
        mw = SoftRateLimitMiddleware(callback_interval_sec=1.0, message_interval_sec=1.0)
        cb = _make_callback(1, 'same')
        seen = []

        async def handler(event, data):
            seen.append('handled')
            return 'ok'

        assert await mw(handler, cb, {}) == 'ok'
        assert await mw(handler, cb, {}) is None
        assert seen == ['handled']
        assert len(cb.answer.calls) == 1


    @pytest.mark.asyncio
    async def test_quick_ack_answers_callback_only_once_even_if_handler_replies_again():
        mw = QuickAckCallbackMiddleware()
        cb = _make_callback(1, 'same')
        seen = []

        async def handler(event, data):
            seen.append('handled')
            await event.answer('later')
            await event.answer('again')
            return 'ok'

        assert await mw(handler, cb, {}) == 'ok'
        assert seen == ['handled']
        assert len(cb.answer.calls) == 1

