import importlib


def _reload_setup(monkeypatch):
    import config.settings as settings_mod
    import services.messenger.setup as setup_mod
    import runtime.telegram_transport as transport_mod
    importlib.reload(settings_mod)
    importlib.reload(transport_mod)
    importlib.reload(setup_mod)
    return setup_mod


def test_max_webhook_requires_secret(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'dev')
    monkeypatch.setenv('MAX_BOT_TOKEN', 'token')
    monkeypatch.setenv('MAX_BOT_LINK_BASE', 'https://max.ru/bot?start={payload}')
    monkeypatch.setenv('MESSENGER_PUBLIC_BASE_URL', 'https://metrotherapy.example')
    monkeypatch.delenv('MAX_WEBHOOK_SECRET', raising=False)
    setup_mod = _reload_setup(monkeypatch)

    status = setup_mod.build_setup_status()

    assert status.max_ok is True
    assert status.max_webhook_ok is False
    assert 'MAX_WEBHOOK_SECRET' in status.missing


def test_max_webhook_ready_with_https_public_base_and_secret(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'dev')
    monkeypatch.setenv('MAX_BOT_TOKEN', 'token')
    monkeypatch.setenv('MAX_BOT_LINK_BASE', 'https://max.ru/bot?start={payload}')
    monkeypatch.setenv('MAX_WEBHOOK_SECRET', 'secret')
    monkeypatch.setenv('MESSENGER_PUBLIC_BASE_URL', 'https://metrotherapy.example')
    monkeypatch.setenv('MESSENGER_WEBHOOK_ENABLED', '1')
    setup_mod = _reload_setup(monkeypatch)

    status = setup_mod.build_setup_status()

    assert status.max_ok is True
    assert status.max_webhook_ok is True
    assert status.max_webhook_url == 'https://metrotherapy.example/webhooks/max'
    assert 'MAX_WEBHOOK_SECRET' not in status.missing


def test_max_webhook_rejects_non_https_public_base(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'dev')
    monkeypatch.setenv('MAX_BOT_TOKEN', 'token')
    monkeypatch.setenv('MAX_BOT_LINK_BASE', 'https://max.ru/bot?start={payload}')
    monkeypatch.setenv('MAX_WEBHOOK_SECRET', 'secret')
    monkeypatch.setenv('MESSENGER_PUBLIC_BASE_URL', 'http://metrotherapy.example')
    setup_mod = _reload_setup(monkeypatch)

    status = setup_mod.build_setup_status()

    assert status.max_ok is True
    assert status.max_webhook_ok is False
    assert any('https://' in warning for warning in status.warnings)
