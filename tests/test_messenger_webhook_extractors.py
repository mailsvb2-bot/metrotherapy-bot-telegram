from runtime.messenger_payloads import extract_max_message, extract_vk_message


def test_extract_vk_message():
    payload = {'type': 'message_new', 'object': {'message': {'from_id': 42, 'text': 'start'}}}
    extracted = extract_vk_message(payload)
    assert extracted is not None
    assert extracted['user_id'] == 42
    assert extracted['text'] == 'start'


def test_extract_max_message():
    payload = {
        'update_type': 'message_created',
        'message': {
            'sender': {'user_id': 77, 'first_name': 'Max'},
            'body': {'text': '/platform vk'},
        },
    }
    extracted = extract_max_message(payload)
    assert extracted is not None
    assert extracted['user_id'] == 77
    assert extracted['text'] == '/platform vk'
