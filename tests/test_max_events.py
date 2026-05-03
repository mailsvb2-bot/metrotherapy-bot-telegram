from services.messenger.max_events import extract_max_inbound_message, max_event_key


def test_extract_max_message_created_text_body():
    payload = {
        'update_id': 'u1',
        'message': {
            'id': 'm1',
            'sender': {'user_id': 42, 'username': 'sergey', 'first_name': 'Сергей'},
            'body': {'text': '🌿 Попробовать бесплатно'},
        },
    }

    msg = extract_max_inbound_message(payload)

    assert msg is not None
    assert msg.user_id == 42
    assert msg.external_user_id == '42'
    assert msg.username == 'sergey'
    assert msg.first_name == 'Сергей'
    assert msg.text == '🌿 Попробовать бесплатно'


def test_extract_max_message_callback_payload_command():
    payload = {
        'update_id': 'u2',
        'callback': {
            'id': 'cb1',
            'payload': 'demo_work',
            'sender': {'id': 77, 'name': 'User'},
        },
    }

    msg = extract_max_inbound_message(payload)

    assert msg is not None
    assert msg.user_id == 77
    assert msg.text == 'demo_work'


def test_extract_max_message_callback_json_payload_command():
    payload = {
        'event_id': 'e3',
        'callback': {
            'id': 'cb2',
            'payload': '{"command":"weather_city"}',
            'user': {'user_id': '88'},
        },
    }

    msg = extract_max_inbound_message(payload)

    assert msg is not None
    assert msg.user_id == 88
    assert msg.text == 'weather_city'


def test_extract_max_message_callback_button_text_fallback():
    payload = {
        'callback': {
            'id': 'cb3',
            'text': '✅ Прослушал',
            'sender': {'user_id': 99},
        },
    }

    msg = extract_max_inbound_message(payload)

    assert msg is not None
    assert msg.text == '✅ Прослушал'


def test_max_event_key_prefers_stable_update_and_message_parts():
    payload = {
        'update_id': 'u1',
        'message': {'id': 'm1', 'sender': {'user_id': 42}, 'created_at': 123},
    }

    assert max_event_key(payload) == 'u1:m1:42:123'


def test_max_event_key_falls_back_to_hash_for_unknown_shape():
    payload = {'unknown': {'nested': True}}

    key = max_event_key(payload)

    assert key.startswith('max:sha256:')
