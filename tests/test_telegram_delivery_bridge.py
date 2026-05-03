import pytest

from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse
from interfaces.messaging.telegram.delivery import send_canonical_telegram_response


class FakeTelegramSender:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append((chat_id, text, kwargs))
        if self.fail:
            raise RuntimeError('boom')
        return {'ok': True}


def test_send_canonical_telegram_response_uses_rendered_inline_keyboard(event_loop, monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.telegram.delivery.observe', lambda *args, **kwargs: observed.append((args, kwargs)))
    sender = FakeTelegramSender()
    response = CanonicalResponse(
        text='Главное меню',
        buttons=((CanonicalButton(text='🌿 Попробовать бесплатно', action='demo'),),),
    )

    result = event_loop.run_until_complete(send_canonical_telegram_response(sender, 42, response))

    assert result == {'ok': True}
    assert sender.calls[0][0] == 42
    assert sender.calls[0][1] == 'Главное меню'
    assert sender.calls[0][2]['reply_markup'] == {
        'inline_keyboard': [[{'text': '🌿 Попробовать бесплатно', 'callback_data': 'demo'}]]
    }
    assert observed == [(('telegram', 'delivery', 'ok'), {'has_buttons': True})]


def test_send_canonical_telegram_response_without_buttons_sends_plain_text(event_loop, monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.telegram.delivery.observe', lambda *args, **kwargs: observed.append((args, kwargs)))
    sender = FakeTelegramSender()
    response = CanonicalResponse(text='Просто текст')

    event_loop.run_until_complete(send_canonical_telegram_response(sender, '42', response))

    assert sender.calls == [('42', 'Просто текст', {})]
    assert observed == [(('telegram', 'delivery', 'ok'), {'has_buttons': False})]


def test_send_canonical_telegram_response_observes_and_reraises_errors(event_loop, monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.telegram.delivery.observe', lambda *args, **kwargs: observed.append((args, kwargs)))
    sender = FakeTelegramSender(fail=True)

    with pytest.raises(RuntimeError):
        event_loop.run_until_complete(send_canonical_telegram_response(sender, '42', CanonicalResponse(text='x')))

    assert observed == [(('telegram', 'delivery', 'error'), {'has_buttons': False, 'error_type': 'RuntimeError'})]
