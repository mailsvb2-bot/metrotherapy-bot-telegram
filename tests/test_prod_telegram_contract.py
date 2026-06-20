from __future__ import annotations

import pytest

from services.validators.base import ValidationError
from services.validators.prod import validate_prod_telegram_polling_contract


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_ENV",
        "TELEGRAM_TRANSPORT",
        "RUN_MODE",
        "TELEGRAM_WEBHOOK_ENABLED",
        "TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_prod_telegram_contract_rejects_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_ENABLED", "1")

    with pytest.raises(ValidationError, match="polling"):
        validate_prod_telegram_polling_contract(strict=True)


def test_prod_telegram_contract_accepts_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "polling")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_ENABLED", "0")

    validate_prod_telegram_polling_contract(strict=True)
