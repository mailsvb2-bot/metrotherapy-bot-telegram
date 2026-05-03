import pytest

from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse
from interfaces.messaging.max.delivery import send_canonical_max_response


class FakeMaxSender:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    async def send_text(self, external_user_id, text, **kwargs):
        self.calls.append((external_user_id, text, kwargs))
        if self.fail:
            raise RuntimeError('boom')
        return {'ok': True}


def test_send_canonical_max_response_uses_rendered_keyboard(event_loop, monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.max.delivery.observe', lambda *args, **kwargs: observed.append((args, kwargs)))
    sender = FakeMaxSender()
    response = CanonicalResponse(
        text='Главное меню',
        buttons=((CanonicalButton(text='🌿 Попробовать бесплатно', action='demo'),),),
    )

    result = event_loop.run_until_complete(send_canonical_max_response(sender, '42', response))

    assert result == {'ok': True}
    assert sender.calls[0][0] == '42'
    assert sender.calls[0][1] == 'Главное меню'
    keyboard = sender.calls[0][2]['max_keyboard']
    assert keyboard['type'] == 'inline_keyboard'
    assert keyboard['payload']['buttons'][0][0] == {
        'type': 'message',
        'text': '🌿 Попробовать бесплатно',
        'payload': 'demo',
    }
    assert observed == [(('max', 'delivery', 'ok'), {'has_buttons': True})]


def test_send_canonical_max_response_without_buttons_sends_plain_text(event_loop, monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.max.delivery.observe', lambda *args, **kwargs: observed.append((args, kwargs)))
    sender = FakeMaxSender()
    response = CanonicalResponse(text='Просто текст')

    event_loop.run_until_complete(send_canonical_max_response(sender, '42', response))

    assert sender.calls == [('42', 'Просто текст', {'max_keyboard': None})]
    assert observed == [(('max', 'delivery', 'ok'), {'has_buttons': False})]


def test_send_canonical_max_response_observes_and_reraises_errors(event_loop, monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.max.delivery.observe', lambda *args, **kwargs: observed.append((args, kwargs)))
    sender = FakeMaxSender(fail=True)

    with pytest.raises(RuntimeError):
        event_loop.run_until_complete(send_canonical_max_response(sender, '42', CanonicalResponse(text='x')))

    assert observed == [(('max', 'delivery', 'error'), {'has_buttons': False, 'error_type': 'RuntimeError'})]
