from interfaces.messaging.contracts import ConversationEvent, ConversationUser
from interfaces.messaging.conversation_bridge import handle_conversation_event


def test_conversation_event_bridges_to_canonical_menu_response(monkeypatch):
    captured = {}

    def fake_handle_incoming_text(user_id, *, platform, external_user_id, text, username=None, display_name=None, first_name=None):
        captured.update(
            {
                'user_id': user_id,
                'platform': platform,
                'external_user_id': external_user_id,
                'text': text,
                'username': username,
                'display_name': display_name,
                'first_name': first_name,
            }
        )
        from services.messenger.text_ui import MessengerReply
        return 42, [MessengerReply(text='Главное меню\n\nВыберите маршрут')]

    monkeypatch.setattr('interfaces.messaging.conversation_bridge.handle_incoming_text', fake_handle_incoming_text)

    event = ConversationEvent(
        platform='max',
        kind='message',
        user=ConversationUser(
            user_id=7,
            external_user_id='max-7',
            platform='max',
            username='sergey',
            display_name='Сергей',
            first_name='Сергей',
        ),
        text='start',
        event_key='max:1',
        raw={'update_id': 'max:1'},
    )

    canonical_user_id, responses = handle_conversation_event(event)

    assert canonical_user_id == 42
    assert captured == {
        'user_id': 7,
        'platform': 'max',
        'external_user_id': 'max-7',
        'text': 'start',
        'username': 'sergey',
        'display_name': 'Сергей',
        'first_name': 'Сергей',
    }
    assert len(responses) == 1
    assert responses[0].text.startswith('Главное меню')
    assert [button.text for row in responses[0].buttons for button in row] == [
        '🌿 Попробовать бесплатно',
        '🔐 Полный маршрут',
        '💳 Тарифы',
        '🎁 Подарить',
        '📈 Мой прогресс',
        '🧠 Настройки',
        '📣 Посоветовать',
        '🌤 Погода',
    ]


def test_conversation_event_bridge_contains_no_transport_rendering(monkeypatch):
    def fake_handle_incoming_text(*args, **kwargs):
        from services.messenger.text_ui import MessengerReply
        return 1, [MessengerReply(text='Просто текст')]

    monkeypatch.setattr('interfaces.messaging.conversation_bridge.handle_incoming_text', fake_handle_incoming_text)
    event = ConversationEvent(
        platform='max',
        kind='message',
        user=ConversationUser(user_id=1, external_user_id='1', platform='max'),
        text='hello',
        event_key='e1',
    )

    _, responses = handle_conversation_event(event)

    assert responses[0].text == 'Просто текст'
    assert responses[0].buttons == ()
    assert 'attachments' not in responses[0].meta
