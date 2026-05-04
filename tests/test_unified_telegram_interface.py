from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse
from interfaces.messaging.telegram.adapter import adapt_telegram_update, telegram_event_key
from interfaces.messaging.telegram.renderer import render_telegram_response


def test_telegram_adapter_returns_conversation_event_from_message(monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.telegram.adapter.observe', lambda *args, **kwargs: observed.append((args, kwargs)))
    update = {
        'update_id': 100,
        'message': {
            'message_id': 5,
            'from': {'id': 42, 'username': 'sergey', 'first_name': 'Сергей'},
            'text': '/start',
        },
    }

    event = adapt_telegram_update(update)

    assert event is not None
    assert event.platform == 'telegram'
    assert event.kind == 'start'
    assert event.user.user_id == 42
    assert event.user.external_user_id == '42'
    assert event.user.username == 'sergey'
    assert event.text == '/start'
    assert event.event_key == '100:5:42'
    assert observed == [(('telegram', 'inbound', 'ok'), {'kind': 'start', 'has_text': True})]


def test_telegram_adapter_returns_conversation_event_from_callback_query(monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.telegram.adapter.observe', lambda *args, **kwargs: observed.append((args, kwargs)))
    update = {
        'update_id': 101,
        'callback_query': {
            'id': 'cb1',
            'from': {'id': '77', 'first_name': 'User'},
            'message': {'message_id': 9},
            'data': 'demo_work',
        },
    }

    event = adapt_telegram_update(update)

    assert event is not None
    assert event.platform == 'telegram'
    assert event.kind == 'button'
    assert event.user.user_id == 77
    assert event.text == 'demo_work'
    assert event.event_key == '101:9:77'
    assert observed == [(('telegram', 'inbound', 'ok'), {'kind': 'button', 'has_text': True})]


def test_telegram_adapter_observes_rejected_update(monkeypatch):
    observed = []
    monkeypatch.setattr('interfaces.messaging.telegram.adapter.observe', lambda *args, **kwargs: observed.append((args, kwargs)))

    assert adapt_telegram_update({'unknown': True}) is None
    assert observed == [(('telegram', 'inbound', 'rejected'), {'reason': 'unsupported_update'})]


def test_telegram_event_key_falls_back_to_hash():
    assert telegram_event_key({'unknown': True}).startswith('telegram:sha256:')


def test_telegram_renderer_turns_canonical_response_into_inline_keyboard():
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

    rendered = render_telegram_response(response)

    assert rendered.text == 'Главное меню'
    assert rendered.payload['text'] == 'Главное меню'
    assert rendered.payload['reply_markup']['inline_keyboard'][0][0] == {
        'text': '🌿 Попробовать бесплатно',
        'callback_data': 'demo',
    }
    assert rendered.payload['reply_markup']['inline_keyboard'][1][0] == {
        'text': '💳 Тарифы',
        'url': 'https://example.test/pay',
    }
