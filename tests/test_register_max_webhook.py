import json

import scripts.register_max_webhook as register


def test_load_config_rejects_legacy_botapi_domain(monkeypatch):
    monkeypatch.setenv('MAX_API_BASE_URL', 'https://botapi.max.ru')
    monkeypatch.setenv('MAX_BOT_TOKEN', 'token')
    monkeypatch.setenv('MAX_WEBHOOK_SECRET', 'secret')
    monkeypatch.setenv('MESSENGER_PUBLIC_BASE_URL', 'https://metrotherapy.example')

    try:
        register._load_config()
    except SystemExit as exc:
        assert 'botapi.max.ru' in str(exc)
    else:  # pragma: no cover
        raise AssertionError('legacy MAX domain must be rejected')


def test_register_max_webhook_uses_me_and_subscription_preflight(monkeypatch, capsys):
    calls = []

    class FakeResponse:
        def __init__(self, data):
            self.data = data
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps(self.data).encode('utf-8')

    def fake_urlopen(request, timeout=30):
        calls.append({
            'url': request.full_url,
            'method': request.get_method(),
            'headers': dict(request.header_items()),
            'body': request.data,
        })
        if request.full_url.endswith('/me'):
            return FakeResponse({'user_id': 1, 'username': 'metro_bot', 'is_bot': True})
        if request.full_url.endswith('/subscriptions') and request.get_method() == 'GET':
            if len([c for c in calls if c['url'].endswith('/subscriptions') and c['method'] == 'GET']) == 1:
                return FakeResponse({'subscriptions': []})
            return FakeResponse({'subscriptions': [{'url': 'https://metrotherapy.example/webhooks/max'}]})
        if request.full_url.endswith('/subscriptions') and request.get_method() == 'POST':
            return FakeResponse({'success': True})
        return FakeResponse({})

    monkeypatch.setenv('MAX_API_BASE_URL', 'https://platform-api.max.ru')
    monkeypatch.setenv('MAX_BOT_TOKEN', 'token')
    monkeypatch.setenv('MAX_WEBHOOK_SECRET', 'secret')
    monkeypatch.setenv('MESSENGER_PUBLIC_BASE_URL', 'https://metrotherapy.example')
    monkeypatch.setattr('urllib.request.urlopen', fake_urlopen)

    assert register.main() == 0

    urls_methods = [(call['method'], call['url']) for call in calls]
    assert urls_methods == [
        ('GET', 'https://platform-api.max.ru/me'),
        ('GET', 'https://platform-api.max.ru/subscriptions'),
        ('POST', 'https://platform-api.max.ru/subscriptions'),
        ('GET', 'https://platform-api.max.ru/subscriptions'),
    ]
    assert all(call['headers'].get('Authorization') == 'token' for call in calls)
    assert all('token=' not in call['url'] and 'access_token=' not in call['url'] for call in calls)

    post = next(call for call in calls if call['method'] == 'POST')
    payload = json.loads(post['body'].decode('utf-8'))
    assert payload['url'] == 'https://metrotherapy.example/webhooks/max'
    assert 'message_created' in payload['update_types']
    assert 'message_callback' in payload['update_types']
    assert payload['secret'] == 'secret'

    output = json.loads(capsys.readouterr().out)
    assert output['success'] is True
    assert output['mode'] == 'webhook'
    assert output['webhook_url'] == 'https://metrotherapy.example/webhooks/max'
