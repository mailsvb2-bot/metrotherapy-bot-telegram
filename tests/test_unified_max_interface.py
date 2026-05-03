import json

from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse
from interfaces.messaging.max.adapter import adapt_max_event
from interfaces.messaging.max.renderer import render_max_response
from interfaces.messaging.max.transport import MaxTransportClient, MaxTransportConfig


def test_max_adapter_returns_conversation_event_without_business_logic():
    payload = {
        'update_id': 'u1',
        'message': {
            'id': 'm1',
            'sender': {'user_id': 42, 'username': 'sergey'},
            'body': {'text': '🌿 Попробовать бесплатно'},
        },
    }

    event = adapt_max_event(payload)

    assert event is not None
    assert event.platform == 'max'
    assert event.kind == 'message'
    assert event.user.user_id == 42
    assert event.user.external_user_id == '42'
    assert event.text == '🌿 Попробовать бесплатно'
    assert event.event_key == 'u1:m1:42'
    assert event.raw is payload


def test_max_renderer_turns_canonical_response_into_inline_keyboard():
    response = CanonicalResponse(
        text='Главное меню',
        buttons=(
            (
                CanonicalButton(text='🌿 Попробовать бесплатно', action='demo'),
                CanonicalButton(text='🔐 Полный маршрут', action='full'),
            ),
            (
                CanonicalButton(text='💳 Тарифы', action='pay', kind='link', url='https://example.test/pay'),
            ),
        ),
    )

    rendered = render_max_response(response)

    assert rendered.text == 'Главное меню'
    assert rendered.payload['text'] == 'Главное меню'
    keyboard = rendered.payload['attachments'][0]
    assert keyboard['type'] == 'inline_keyboard'
    assert keyboard['payload']['buttons'][0][0] == {
        'type': 'message',
        'text': '🌿 Попробовать бесплатно',
        'payload': 'demo',
    }
    assert keyboard['payload']['buttons'][1][0] == {
        'type': 'link',
        'text': '💳 Тарифы',
        'url': 'https://example.test/pay',
    }


def test_max_transport_rejects_legacy_domain_and_uses_authorization_header(monkeypatch):
    try:
        MaxTransportConfig(token='token', api_base_url='https://botapi.max.ru')
    except ValueError as exc:
        assert 'botapi.max.ru' in str(exc)
    else:  # pragma: no cover
        raise AssertionError('legacy MAX domain must be rejected')

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps({'ok': True}).encode('utf-8')

    def fake_urlopen(request, timeout=20):
        captured['url'] = request.full_url
        captured['method'] = request.get_method()
        captured['headers'] = dict(request.header_items())
        captured['body'] = request.data
        return FakeResponse()

    monkeypatch.setattr('urllib.request.urlopen', fake_urlopen)
    client = MaxTransportClient(MaxTransportConfig(token='secret-token'))
    assert client.send_message(user_id='42', payload={'text': 'hello'}) == {'ok': True}

    assert captured['method'] == 'POST'
    assert captured['headers']['Authorization'] == 'secret-token'
    assert captured['url'].startswith('https://platform-api.max.ru/messages?user_id=42')
    assert 'token=' not in captured['url']
    assert 'access_token=' not in captured['url']
    assert json.loads(captured['body'].decode('utf-8')) == {'text': 'hello'}
