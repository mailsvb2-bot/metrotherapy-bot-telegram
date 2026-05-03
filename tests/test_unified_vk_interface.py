import json

from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse
from interfaces.messaging.vk.adapter import adapt_vk_event, vk_event_key
from interfaces.messaging.vk.renderer import render_vk_response
from interfaces.messaging.vk.delivery import send_canonical_vk_response


def test_vk_adapter_returns_conversation_event_from_text_message():
    payload = {
        'event_id': 'e1',
        'object': {
            'message': {
                'id': 10,
                'from_id': 42,
                'date': 123,
                'text': '🌿 Попробовать бесплатно',
            }
        },
    }

    event = adapt_vk_event(payload)

    assert event is not None
    assert event.platform == 'vk'
    assert event.kind == 'message'
    assert event.user.user_id == 42
    assert event.user.external_user_id == '42'
    assert event.text == 'demo'
    assert event.event_key == 'e1:10:42:123'


def test_vk_adapter_returns_conversation_event_from_payload_button():
    payload = {
        'object': {
            'message': {
                'conversation_message_id': 7,
                'from_id': '77',
                'payload': '{"command":"weather_city"}',
            }
        }
    }

    event = adapt_vk_event(payload)

    assert event is not None
    assert event.platform == 'vk'
    assert event.kind == 'button'
    assert event.user.user_id == 77
    assert event.text == 'weather_city'


def test_vk_event_key_falls_back_to_hash():
    assert vk_event_key({'unknown': True}).startswith('vk:sha256:')


def test_vk_renderer_turns_canonical_response_into_keyboard_json():
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

    rendered = render_vk_response(response)

    assert rendered.text == 'Главное меню'
    keyboard = json.loads(rendered.payload['keyboard_json'])
    assert keyboard['one_time'] is False
    assert keyboard['inline'] is False
    assert keyboard['buttons'][0][0]['action'] == {
        'type': 'text',
        'label': '🌿 Попробовать бесплатно',
        'payload': '{"command": "demo"}',
    }
    assert keyboard['buttons'][1][0]['action'] == {
        'type': 'open_link',
        'label': '💳 Тарифы',
        'link': 'https://example.test/pay',
    }


class FakeVkSender:
    def __init__(self):
        self.calls = []

    async def send_text(self, external_user_id, text, **kwargs):
        self.calls.append((external_user_id, text, kwargs))
        return {'ok': True}


def test_vk_delivery_bridge_sends_rendered_keyboard(event_loop):
    sender = FakeVkSender()
    response = CanonicalResponse(
        text='Главное меню',
        buttons=((CanonicalButton(text='🌿 Попробовать бесплатно', action='demo'),),),
    )

    result = event_loop.run_until_complete(send_canonical_vk_response(sender, '42', response))

    assert result == {'ok': True}
    assert sender.calls[0][0] == '42'
    assert sender.calls[0][1] == 'Главное меню'
    keyboard = json.loads(sender.calls[0][2]['keyboard_json'])
    assert keyboard['buttons'][0][0]['action']['payload'] == '{"command": "demo"}'
