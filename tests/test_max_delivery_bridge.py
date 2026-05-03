from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse
from interfaces.messaging.max.delivery import send_canonical_max_response


class FakeMaxSender:
    def __init__(self):
        self.calls = []

    async def send_text(self, external_user_id, text, **kwargs):
        self.calls.append((external_user_id, text, kwargs))
        return {'ok': True}


def test_send_canonical_max_response_uses_rendered_keyboard(event_loop):
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


def test_send_canonical_max_response_without_buttons_sends_plain_text(event_loop):
    sender = FakeMaxSender()
    response = CanonicalResponse(text='Просто текст')

    event_loop.run_until_complete(send_canonical_max_response(sender, '42', response))

    assert sender.calls == [('42', 'Просто текст', {'max_keyboard': None})]
