from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from services.messenger import preflight
from services.validators import prod
from services.validators.base import ValidationError


ROOT = Path(__file__).resolve().parents[1]
MIGRATOR = ROOT / "scripts" / "migrate_payment_guard_env.py"
DEPLOY = ROOT / "deploy.sh"


def _load_migrator():
    spec = importlib.util.spec_from_file_location("payment_guard_env_migration_contract", MIGRATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_guards_without_touching_secrets(tmp_path: Path) -> None:
    module = _load_migrator()
    env_file = tmp_path / "metrotherapy.env"
    secret_line = "YOOKASSA_SECRET_KEY=secret-value-that-must-stay-identical\n"
    env_file.write_text("APP_ENV=prod\n" + secret_line + "OTHER=value\n", encoding="utf-8")
    env_file.chmod(0o600)

    result = module.migrate_env_file(env_file)

    text = env_file.read_text(encoding="utf-8")
    assert result.changed is True
    assert result.backup_path is not None
    assert result.backup_path.read_text(encoding="utf-8") == "APP_ENV=prod\n" + secret_line + "OTHER=value\n"
    assert secret_line in text
    assert "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED=1\n" in text
    assert "PAYMENT_CHECKOUT_INTENT_REQUIRED=1\n" in text


def test_migration_repairs_disabled_values_and_is_idempotent(tmp_path: Path) -> None:
    module = _load_migrator()
    env_file = tmp_path / "metrotherapy.env"
    env_file.write_text(
        "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED=0\n"
        "PAYMENT_CHECKOUT_INTENT_REQUIRED=false\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    first = module.migrate_env_file(env_file)
    second = module.migrate_env_file(env_file)

    assert first.changed is True
    assert second.changed is False
    assert env_file.read_text(encoding="utf-8").count("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED=1") == 1
    assert env_file.read_text(encoding="utf-8").count("PAYMENT_CHECKOUT_INTENT_REQUIRED=1") == 1


def test_migration_rejects_duplicate_managed_keys(tmp_path: Path) -> None:
    module = _load_migrator()
    env_file = tmp_path / "metrotherapy.env"
    original = (
        "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED=1\n"
        "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED=0\n"
    )
    env_file.write_text(original, encoding="utf-8")
    env_file.chmod(0o600)

    with pytest.raises(module.MigrationError, match="duplicate managed environment keys"):
        module.migrate_env_file(env_file)

    assert env_file.read_text(encoding="utf-8") == original


def _prod_payment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "APP_ENV": "prod",
        "TOKEN_ECONOMY_ENABLED": "1",
        "TOKEN_ENFORCEMENT_MODE": "hard",
        "YOOKASSA_RECEIPT_EMAIL": "ops@example.test",
        "YOOKASSA_TAX_SYSTEM_CODE": "2",
        "YOOKASSA_VAT_CODE": "1",
        "YOOKASSA_PAYMENT_SUBJECT": "service",
        "YOOKASSA_PAYMENT_MODE": "full_payment",
        "TELEGRAM_STARS_ENABLED": "0",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ALLOW_UNVERIFIED_YOOKASSA_WEBHOOK_IN_PROD", raising=False)
    monkeypatch.delenv("ALLOW_UNSIGNED_PAYMENT_CHECKOUT_IN_PROD", raising=False)


def test_prod_validator_rejects_missing_mandatory_payment_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_payment_env(monkeypatch)
    monkeypatch.delenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", raising=False)
    monkeypatch.delenv("PAYMENT_CHECKOUT_INTENT_REQUIRED", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        prod.validate_prod_monetization_contract(strict=True)

    message = str(exc_info.value)
    assert "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED must be enabled in prod" in message
    assert "PAYMENT_CHECKOUT_INTENT_REQUIRED must be enabled in prod" in message


def test_prod_validator_accepts_enabled_payment_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_payment_env(monkeypatch)
    monkeypatch.setenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", "1")
    monkeypatch.setenv("PAYMENT_CHECKOUT_INTENT_REQUIRED", "true")

    prod.validate_prod_monetization_contract(strict=True)


def test_payment_preflight_rejects_missing_guard_even_when_checkout_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preflight, "payment_http_enabled", lambda: True)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "test-shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "signing-key")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://payments.example.test")
    monkeypatch.delenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", raising=False)
    monkeypatch.setenv("PAYMENT_CHECKOUT_INTENT_REQUIRED", "1")

    status = preflight.check_payment_preflight()

    assert status.ok is False
    assert "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED(must_be_enabled)" in status.missing


def test_deploy_migrates_guards_before_candidate_preparation() -> None:
    source = DEPLOY.read_text(encoding="utf-8")

    migration = source.index('"$SYSTEM_PYTHON" "$PAYMENT_GUARD_MIGRATOR" --env-file "$ENV_FILE"')
    candidate = source.index('bash "$CANDIDATE_PREPARER" "$SOURCE_DIR"')
    immutable = source.index('bash "$SOURCE_DIR/scripts/immutable_deploy.sh"')

    assert migration < candidate < immutable
    assert "migrate_payment_guard_env.py" in source
