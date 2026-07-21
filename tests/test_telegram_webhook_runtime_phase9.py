from __future__ import annotations

import json
from typing import Any

import aiogram.types as aiogram_types
import pytest
from aiohttp import web

from runtime import telegram_webhook_runtime as runtime


class RequestDouble:
    def __init__(
        self,
        *,
        app: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        match_info: dict[str, str] | None = None,
        payload: Any = None,
        json_error: BaseException | None = None,
    ) -> None:
        self.app = app or {}
        self.headers = headers or {}
        self.match_info = match_info or {}
        self.payload = {} if payload is None else payload
        self.json_error = json_error

    async def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class DispatcherDouble:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    async def feed_webhook_update(self, bot: Any, update: Any) -> None:
        self.calls.append((bot, update))


class TaskManagerDouble:
    def __init__(self) -> None:
        self.coro = None
        self.name = None

    def create(self, coro, *, name: str) -> None:
        self.coro = coro
        self.name = name


class ModernUpdate:
    validated: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    @classmethod
    def model_validate(cls, payload: dict[str, Any], context: dict[str, Any]):
        cls.validated.append((payload, context))
        return cls(payload)


class LegacyUpdate:
    def __init__(self, **payload: Any) -> None:
        self.payload = payload


def install_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret: str = "secret",
    bot_token: str = "bot-token",
    prefix: str = "/telegram-webhook",
    public_base: str = "https://bot.example",
) -> None:
    monkeypatch.setattr(runtime.settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", secret)
    monkeypatch.setattr(runtime.settings, "BOT_TOKEN", bot_token)
    monkeypatch.setattr(runtime.settings, "TELEGRAM_WEBHOOK_PREFIX", prefix)
    monkeypatch.setattr(runtime.settings, "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", public_base)


def configured_request(
    *,
    update_payload: dict[str, Any] | None = None,
    route_token: str = "",
    secret: str = "secret",
    task_manager: Any = None,
) -> tuple[RequestDouble, object, DispatcherDouble]:
    bot = object()
    dispatcher = DispatcherDouble()
    request = RequestDouble(
        app={
            "telegram_bot": bot,
            "telegram_dispatcher": dispatcher,
            "task_manager": task_manager,
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        match_info={"bot_token": route_token},
        payload=update_payload or {"update_id": 1},
    )
    return request, bot, dispatcher


def test_env_app_and_insecure_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHASE9_BOOL", raising=False)
    assert runtime._env_bool("PHASE9_BOOL") is False
    assert runtime._env_bool("PHASE9_BOOL", True) is True
    for raw in ("1", "true", "YES", "on"):
        monkeypatch.setenv("PHASE9_BOOL", raw)
        assert runtime._env_bool("PHASE9_BOOL") is True
    monkeypatch.setenv("PHASE9_BOOL", "webhook")
    assert runtime._env_bool("PHASE9_BOOL") is False

    monkeypatch.setenv("APP_ENV", " PROD ")
    assert runtime._app_env() == "prod"
    monkeypatch.setenv("ALLOW_INSECURE_TELEGRAM_WEBHOOK", "1")
    assert runtime._allow_insecure_telegram_webhook() is False

    monkeypatch.setenv("APP_ENV", "stage")
    assert runtime._allow_insecure_telegram_webhook() is False
    monkeypatch.setenv("APP_ENV", "dev")
    assert runtime._allow_insecure_telegram_webhook() is True


def test_webhook_paths_and_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    install_runtime_settings(monkeypatch, prefix=" telegram/ ", public_base="https://bot.example/")
    assert runtime.telegram_webhook_prefix() == "/telegram"
    assert runtime.telegram_webhook_path() == "/telegram"
    assert runtime.telegram_legacy_webhook_path() == "/telegram/{bot_token}"
    assert runtime.telegram_public_webhook_url() == "https://bot.example/telegram"

    monkeypatch.setattr(runtime.settings, "TELEGRAM_WEBHOOK_PREFIX", "/")
    assert runtime.telegram_webhook_prefix() == "/telegram-webhook"
    monkeypatch.setattr(runtime.settings, "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", "")
    assert runtime.telegram_public_webhook_url() == ""


def test_secret_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    install_runtime_settings(monkeypatch, secret="expected")
    assert runtime.telegram_secret_ok(RequestDouble(headers={})) is False
    assert runtime.telegram_secret_ok(
        RequestDouble(headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    ) is False
    assert runtime.telegram_secret_ok(
        RequestDouble(headers={"X-Telegram-Bot-Api-Secret-Token": "expected"})
    ) is True

    monkeypatch.setattr(runtime.settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("ALLOW_INSECURE_TELEGRAM_WEBHOOK", "1")
    assert runtime.telegram_secret_ok(RequestDouble()) is True
    monkeypatch.setenv("APP_ENV", "prod")
    assert runtime.telegram_secret_ok(RequestDouble()) is False


@pytest.mark.asyncio
async def test_webhook_requires_configured_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    install_runtime_settings(monkeypatch)
    with pytest.raises(web.HTTPServiceUnavailable) as exc_info:
        await runtime.telegram_webhook(RequestDouble())
    assert exc_info.value.status == 503
    assert exc_info.value.text == "telegram webhook runtime is not configured"


@pytest.mark.asyncio
async def test_webhook_rejects_bad_route_token_and_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    install_runtime_settings(monkeypatch)
    request, _bot, _dispatcher = configured_request(route_token="wrong")
    with pytest.raises(web.HTTPForbidden) as exc_info:
        await runtime.telegram_webhook(request)
    assert exc_info.value.status == 403
    assert exc_info.value.text == "bad token"

    request, _bot, _dispatcher = configured_request(route_token="bot-token", secret="wrong")
    with pytest.raises(web.HTTPForbidden) as exc_info:
        await runtime.telegram_webhook(request)
    assert exc_info.value.status == 403
    assert exc_info.value.text == "bad telegram secret"

    monkeypatch.setattr(runtime.settings, "BOT_TOKEN", "")
    request, _bot, _dispatcher = configured_request(route_token="legacy")
    with pytest.raises(web.HTTPForbidden) as exc_info:
        await runtime.telegram_webhook(request)
    assert exc_info.value.text == "bad token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [json.JSONDecodeError("bad", "", 0), UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")],
)
async def test_webhook_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    install_runtime_settings(monkeypatch)
    request, _bot, _dispatcher = configured_request()
    request.json_error = error
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        await runtime.telegram_webhook(request)
    assert exc_info.value.status == 400
    assert exc_info.value.text == "invalid telegram json"


@pytest.mark.asyncio
async def test_webhook_processes_modern_update_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    install_runtime_settings(monkeypatch)
    ModernUpdate.validated.clear()
    monkeypatch.setattr(aiogram_types, "Update", ModernUpdate)
    payload = {"update_id": 17, "message": {"text": "hello"}}
    request, bot, dispatcher = configured_request(update_payload=payload, route_token="bot-token")

    response = await runtime.telegram_webhook(request)

    assert response.status == 200
    assert json.loads(response.body) == {"ok": True}
    assert ModernUpdate.validated == [(payload, {"bot": bot})]
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0][0] is bot
    assert dispatcher.calls[0][1].payload == payload


@pytest.mark.asyncio
async def test_webhook_supports_legacy_update_and_task_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    install_runtime_settings(monkeypatch)
    monkeypatch.setattr(aiogram_types, "Update", LegacyUpdate)
    manager = TaskManagerDouble()
    payload = {"update_id": 18}
    request, bot, dispatcher = configured_request(update_payload=payload, task_manager=manager)

    response = await runtime.telegram_webhook(request)

    assert response.status == 200
    assert manager.name == "telegram-webhook-update"
    assert dispatcher.calls == []
    assert manager.coro is not None
    await manager.coro
    assert dispatcher.calls[0][0] is bot
    assert dispatcher.calls[0][1].payload == payload
