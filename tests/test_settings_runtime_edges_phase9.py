from __future__ import annotations

import builtins
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.test_settings_runtime_contract_phase9 import cfg, install_valid_prod


def load_isolated_settings(module_name: str) -> ModuleType:
    module_path = Path(cfg.__file__ or "")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_dotenv_load_and_importerror_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    fake_dotenv = ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *, override: calls.append(override)
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("LOAD_DOTENV", raising=False)

    loaded = load_isolated_settings("phase9_settings_with_dotenv")
    assert loaded.APP_ENV == "dev"
    assert calls == [False]

    original_import = builtins.__import__

    def import_without_dotenv(name: str, *args, **kwargs):
        if name == "dotenv":
            raise ImportError("dotenv unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "dotenv", raising=False)
    monkeypatch.setattr(builtins, "__import__", import_without_dotenv)
    loaded_without_dotenv = load_isolated_settings("phase9_settings_without_dotenv")
    assert loaded_without_dotenv.APP_ENV == "dev"


def test_admin_id_field_skips_empty_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    monkeypatch.delenv("ADMIN_ID", raising=False)
    settings = cfg.Settings(ADMIN_IDS="1;; ;bad,2,")
    assert settings.admin_id_list == [1, 2]


def test_empty_telegram_public_url_records_missing_before_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    install_valid_prod(monkeypatch)
    monkeypatch.setattr(cfg.settings, "TELEGRAM_TRANSPORT", "webhook")
    monkeypatch.setattr(cfg.settings, "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(cfg.settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "secret")

    with pytest.raises(SystemExit, match="PUBLIC_BASE_URL must start with https"):
        cfg._fail_fast_prod_config()


def test_valid_max_and_vk_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    install_valid_prod(monkeypatch)
    monkeypatch.setenv("MAX_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("VK_WEBHOOK_ENABLED", "1")
    monkeypatch.setattr(cfg.settings, "MESSENGER_PUBLIC_BASE_URL", "https://messengers.example")
    monkeypatch.setattr(cfg.settings, "MAX_BOT_TOKEN", "max-token")
    monkeypatch.setattr(cfg.settings, "MAX_BOT_LINK_BASE", "https://max.example/bot")
    monkeypatch.setattr(cfg.settings, "MAX_WEBHOOK_SECRET", "max-secret")
    monkeypatch.setattr(cfg.settings, "VK_GROUP_TOKEN", "vk-token")
    monkeypatch.setattr(cfg.settings, "VK_CONFIRMATION_TOKEN", "vk-confirm")
    monkeypatch.setattr(cfg.settings, "VK_GROUP_ID", "42")
    monkeypatch.setattr(cfg.settings, "VK_SECRET", "vk-secret")

    cfg._fail_fast_prod_config()
