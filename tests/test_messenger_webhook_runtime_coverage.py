from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from runtime import messenger_webhooks


class FakeRouter:
    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Any]] = []

    def add_get(self, path: str, handler: Any) -> None:
        self.routes.append(("GET", path, handler))

    def add_post(self, path: str, handler: Any) -> None:
        self.routes.append(("POST", path, handler))


class FakeApplication(dict):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.router = FakeRouter()


class FakeRunner:
    instances: list["FakeRunner"] = []

    def __init__(self, app: Any) -> None:
        self.app = app
        self.setup_calls = 0
        self.cleanup_calls = 0
        self.instances.append(self)

    async def setup(self) -> None:
        self.setup_calls += 1

    async def cleanup(self) -> None:
        self.cleanup_calls += 1


class FakeSite:
    instances: list["FakeSite"] = []
    fail: BaseException | None = None

    def __init__(self, runner: Any, *, host: str, port: int) -> None:
        self.runner = runner
        self.host = host
        self.port = port
        self.start_calls = 0
        self.instances.append(self)

    async def start(self) -> None:
        self.start_calls += 1
        if self.fail is not None:
            raise self.fail


class FakeRequest:
    def __init__(self, body: str = "", headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = headers or {}
        self.cloned_headers: dict[str, str] | None = None

    async def text(self) -> str:
        return self._body

    def clone(self, *, headers: dict[str, str]) -> "FakeRequest":
        clone = FakeRequest(self._body, dict(headers))
        self.cloned_headers = dict(headers)
        return clone


@pytest.mark.asyncio
async def test_runtime_stop_handles_worker_and_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    stops: list[str] = []

    async def stop_worker() -> None:
        stops.append("worker")

    monkeypatch.setattr(messenger_webhooks, "stop_delivery_worker", stop_worker)
    runner = FakeRunner(FakeApplication())
    runtime = messenger_webhooks.MessengerWebhookRuntime(
        runner=runner,
        site=FakeSite(runner, host="127.0.0.1", port=1),
        delivery_worker_started=True,
    )
    await runtime.stop()
    assert stops == ["worker"]
    assert runtime.delivery_worker_started is False
    assert runner.cleanup_calls == 1
    await runtime.stop()
    assert stops == ["worker"]
    assert runner.cleanup_calls == 2


@pytest.mark.asyncio
async def test_health_and_environment_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    response = await messenger_webhooks._health(SimpleNamespace())
    assert response.status == 200
    assert json.loads(response.body) == {"ok": True, "service": "http-ingress"}

    monkeypatch.delenv("FLAG", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(messenger_webhooks, "settings", SimpleNamespace(APP_ENV="dev"))
    assert messenger_webhooks._truthy_env("FLAG") is False
    assert messenger_webhooks._deployed_env() is False
    monkeypatch.setenv("FLAG", " YES ")
    monkeypatch.setenv("APP_ENV", "staging")
    assert messenger_webhooks._truthy_env("FLAG") is True
    assert messenger_webhooks._deployed_env() is True


@pytest.mark.asyncio
async def test_max_official_secret_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, str]] = []

    async def handler(request: FakeRequest) -> Any:
        seen.append(dict(request.headers))
        return "ok"

    monkeypatch.setattr(messenger_webhooks, "max_webhook", handler)
    request = FakeRequest(headers={"X-Max-Bot-Api-Secret": " official "})
    assert await messenger_webhooks._max_webhook_with_official_secret(request) == "ok"
    assert seen[-1]["X-Max-Webhook-Secret"] == "official"
    assert request.cloned_headers is not None

    legacy = FakeRequest(
        headers={
            "X-Max-Bot-Api-Secret": "official",
            "X-Max-Webhook-Secret": "legacy",
        }
    )
    assert await messenger_webhooks._max_webhook_with_official_secret(legacy) == "ok"
    assert seen[-1]["X-Max-Webhook-Secret"] == "legacy"
    assert legacy.cloned_headers is None


@pytest.mark.parametrize(
    ("expected", "payload", "deployed", "allow", "result"),
    [
        ("", {}, False, True, True),
        ("", {}, False, False, False),
        ("", {}, True, True, False),
        ("10", {"group_id": 10}, True, False, True),
        ("10", {"group_id": "11"}, True, False, False),
        ("bad", {"group_id": 10}, True, False, False),
        ("10", {"group_id": "bad"}, True, False, False),
        ("0", {"group_id": 0}, False, False, False),
    ],
)
def test_vk_group_guard_matrix(
    monkeypatch: pytest.MonkeyPatch,
    expected: str,
    payload: dict[str, Any],
    deployed: bool,
    allow: bool,
    result: bool,
) -> None:
    monkeypatch.setattr(messenger_webhooks, "settings", SimpleNamespace(VK_GROUP_ID=expected))
    monkeypatch.setattr(messenger_webhooks, "_deployed_env", lambda: deployed)
    monkeypatch.setattr(messenger_webhooks, "_truthy_env", lambda _name: allow)
    assert messenger_webhooks._vk_group_ok(payload) is result


@pytest.mark.asyncio
async def test_vk_webhook_guard_delegation_and_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    delegated: list[str] = []

    async def handler(request: FakeRequest) -> Any:
        delegated.append(await request.text())
        return "delegated"

    monkeypatch.setattr(messenger_webhooks, "vk_webhook", handler)
    assert await messenger_webhooks._vk_webhook_with_group_guard(FakeRequest("not-json")) == "delegated"

    monkeypatch.setattr(messenger_webhooks, "_vk_group_ok", lambda _payload: False)
    rejected = await messenger_webhooks._vk_webhook_with_group_guard(
        FakeRequest('{"group_id":1}')
    )
    assert rejected.status == 403
    assert rejected.text == "forbidden"

    monkeypatch.setattr(messenger_webhooks, "_vk_group_ok", lambda _payload: True)
    assert await messenger_webhooks._vk_webhook_with_group_guard(
        FakeRequest('{"group_id":1}')
    ) == "delegated"
    assert len(delegated) == 2


@pytest.mark.asyncio
async def test_route_registration_and_telegram_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FakeApplication()
    messenger_webhooks._register_health_routes(app)
    messenger_webhooks._register_payment_routes(app)
    messenger_webhooks._register_max_routes(app)
    messenger_webhooks._register_vk_routes(app)
    messenger_webhooks._register_audio_routes(app)
    paths = [(method, path) for method, path, _handler in app.router.routes]
    assert ("GET", "/") in paths
    assert ("GET", "/terms") in paths
    assert ("POST", "/pay/yookassa/webhook") in paths
    assert ("POST", "/webhooks/max") in paths
    assert ("POST", "/webhooks/vk") in paths
    assert any(path.startswith(messenger_webhooks.AUDIO_MEDIA_PREFIX) for _, path in paths)
    assert any(path.startswith(messenger_webhooks.AUDIO_ACCESS_PREFIX) for _, path in paths)

    with pytest.raises(RuntimeError, match="requires bot and dispatcher"):
        messenger_webhooks._register_telegram_routes(app, bot=None, dispatcher=None)

    monkeypatch.setattr(messenger_webhooks, "telegram_webhook_path", lambda: "/telegram")
    monkeypatch.setattr(
        messenger_webhooks, "telegram_legacy_webhook_path", lambda: "/telegram/legacy"
    )
    monkeypatch.setattr(
        messenger_webhooks, "telegram_public_webhook_url", lambda: "https://example.test/telegram"
    )
    monkeypatch.setenv("TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED", "1")
    dispatcher = SimpleNamespace(workflow_data={"task_manager": "tasks"})
    bot = object()
    public = messenger_webhooks._register_telegram_routes(
        app, bot=bot, dispatcher=dispatcher
    )
    assert public == "https://example.test/telegram"
    assert app["telegram_bot"] is bot
    assert app["telegram_dispatcher"] is dispatcher
    assert app["task_manager"] == "tasks"
    paths = [(method, path) for method, path, _handler in app.router.routes]
    assert ("POST", "/telegram") in paths
    assert ("POST", "/telegram/legacy") in paths

    monkeypatch.setattr(messenger_webhooks, "telegram_public_webhook_url", lambda: "")
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        messenger_webhooks._register_telegram_routes(
            FakeApplication(), bot=bot, dispatcher=dispatcher
        )


def test_resolve_ingress_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        messenger_webhooks,
        "settings",
        SimpleNamespace(
            MESSENGER_WEBHOOK_HOST="127.0.0.1",
            MESSENGER_WEBHOOK_PORT=8081,
            TELEGRAM_WEBHOOK_HOST="127.0.0.1",
            TELEGRAM_WEBHOOK_PORT=8081,
        ),
    )
    assert messenger_webhooks._resolve_ingress_bind(
        ingress_enabled=True, telegram_enabled=False
    ) == ("127.0.0.1", 8081)
    assert messenger_webhooks._resolve_ingress_bind(
        ingress_enabled=False, telegram_enabled=True
    ) == ("127.0.0.1", 8081)

    monkeypatch.setattr(
        messenger_webhooks,
        "settings",
        SimpleNamespace(
            MESSENGER_WEBHOOK_HOST="127.0.0.1",
            MESSENGER_WEBHOOK_PORT=8081,
            TELEGRAM_WEBHOOK_HOST="0.0.0.0",
            TELEGRAM_WEBHOOK_PORT=8082,
        ),
    )
    with pytest.raises(RuntimeError, match="share the same ingress"):
        messenger_webhooks._resolve_ingress_bind(
            ingress_enabled=True, telegram_enabled=True
        )
    assert messenger_webhooks._resolve_ingress_bind(
        ingress_enabled=False, telegram_enabled=True
    ) == ("0.0.0.0", 8082)


def _install_runtime_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeRunner.instances.clear()
    FakeSite.instances.clear()
    FakeSite.fail = None
    monkeypatch.setattr(messenger_webhooks.web, "Application", FakeApplication)
    monkeypatch.setattr(messenger_webhooks.web, "AppRunner", FakeRunner)
    monkeypatch.setattr(messenger_webhooks.web, "TCPSite", FakeSite)
    monkeypatch.setattr(messenger_webhooks, "ingress_body_limit", lambda: 1234)
    monkeypatch.setattr(messenger_webhooks, "payment_webhook_admission_middleware", "middleware")
    monkeypatch.setattr(
        messenger_webhooks,
        "settings",
        SimpleNamespace(
            MESSENGER_WEBHOOK_HOST="127.0.0.1",
            MESSENGER_WEBHOOK_PORT=8081,
            TELEGRAM_WEBHOOK_HOST="127.0.0.1",
            TELEGRAM_WEBHOOK_PORT=8081,
            TELEGRAM_WEBHOOK_SECRET_TOKEN="secret",
            TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES=True,
        ),
    )


@pytest.mark.asyncio
async def test_start_runtime_disabled_and_max_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_runtime_fakes(monkeypatch)
    monkeypatch.setattr(messenger_webhooks, "payment_http_enabled", lambda: False)
    monkeypatch.setattr(messenger_webhooks, "max_webhook_enabled", lambda: False)
    monkeypatch.setattr(messenger_webhooks, "vk_webhook_enabled", lambda: False)
    monkeypatch.setattr(messenger_webhooks, "http_ingress_enabled", lambda: False)
    monkeypatch.setattr(messenger_webhooks, "telegram_transport", lambda: "polling")
    assert await messenger_webhooks.start_messenger_webhook_runtime() is None

    starts: list[str] = []
    monkeypatch.setattr(messenger_webhooks, "max_webhook_enabled", lambda: True)
    monkeypatch.setattr(messenger_webhooks, "http_ingress_enabled", lambda: True)
    monkeypatch.setattr(
        messenger_webhooks, "start_delivery_worker", lambda: starts.append("worker")
    )
    runtime = await messenger_webhooks.start_messenger_webhook_runtime()
    assert runtime is not None
    assert starts == ["worker"]
    assert runtime.delivery_worker_started is True
    assert runtime.runner.setup_calls == 1
    assert runtime.site.start_calls == 1
    assert runtime.runner.app.kwargs == {
        "client_max_size": 1234,
        "middlewares": ["middleware"],
    }


@pytest.mark.asyncio
async def test_start_telegram_runtime_sets_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_runtime_fakes(monkeypatch)
    monkeypatch.setattr(messenger_webhooks, "payment_http_enabled", lambda: True)
    monkeypatch.setattr(messenger_webhooks, "max_webhook_enabled", lambda: False)
    monkeypatch.setattr(messenger_webhooks, "vk_webhook_enabled", lambda: False)
    monkeypatch.setattr(messenger_webhooks, "http_ingress_enabled", lambda: True)
    monkeypatch.setattr(messenger_webhooks, "telegram_transport", lambda: "webhook")
    monkeypatch.setattr(
        messenger_webhooks,
        "_register_telegram_routes",
        lambda app, *, bot, dispatcher: "https://example.test/hook",
    )

    class Bot:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def set_webhook(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    bot = Bot()
    dispatcher = SimpleNamespace(workflow_data={})
    runtime = await messenger_webhooks.start_messenger_webhook_runtime(
        bot=bot, dispatcher=dispatcher
    )
    assert runtime is not None
    assert runtime.telegram_public_url == "https://example.test/hook"
    assert bot.calls == [
        {
            "url": "https://example.test/hook",
            "secret_token": "secret",
            "drop_pending_updates": True,
        }
    ]


@pytest.mark.asyncio
async def test_start_runtime_failure_cleans_worker_and_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_runtime_fakes(monkeypatch)
    monkeypatch.setattr(messenger_webhooks, "payment_http_enabled", lambda: False)
    monkeypatch.setattr(messenger_webhooks, "max_webhook_enabled", lambda: True)
    monkeypatch.setattr(messenger_webhooks, "vk_webhook_enabled", lambda: False)
    monkeypatch.setattr(messenger_webhooks, "http_ingress_enabled", lambda: True)
    monkeypatch.setattr(messenger_webhooks, "telegram_transport", lambda: "webhook")
    monkeypatch.setattr(
        messenger_webhooks,
        "_register_telegram_routes",
        lambda app, *, bot, dispatcher: "https://example.test/hook",
    )
    monkeypatch.setattr(messenger_webhooks, "start_delivery_worker", lambda: None)
    stops: list[str] = []

    async def stop_worker() -> None:
        stops.append("stop")

    monkeypatch.setattr(messenger_webhooks, "stop_delivery_worker", stop_worker)

    class Bot:
        async def set_webhook(self, **_kwargs: Any) -> None:
            raise RuntimeError("webhook failure")

    with pytest.raises(RuntimeError, match="webhook failure"):
        await messenger_webhooks.start_messenger_webhook_runtime(
            bot=Bot(), dispatcher=SimpleNamespace(workflow_data={})
        )
    assert stops == ["stop"]
    assert FakeRunner.instances[-1].cleanup_calls == 1

    FakeSite.fail = OSError("bind")
    stops.clear()
    monkeypatch.setattr(messenger_webhooks, "telegram_transport", lambda: "polling")
    with pytest.raises(OSError, match="bind"):
        await messenger_webhooks.start_messenger_webhook_runtime()
    assert stops == []
    assert FakeRunner.instances[-1].cleanup_calls == 1
