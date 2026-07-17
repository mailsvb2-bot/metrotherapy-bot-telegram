from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "regression_gate.py"

ENV_BLOCK = '''PROD_LIKE_VALIDATOR_ENV = {
    "LOAD_DOTENV": "0",
    "VALIDATOR_RELEASE_MODE": "1",
    "VALIDATOR_GUARDRAILS_STRICT": "1",
    "VALIDATOR_SKIP_AUDIO": "1",
}
'''

ENV_BLOCK_WITH_HERMETIC = '''PROD_LIKE_VALIDATOR_ENV = {
    "LOAD_DOTENV": "0",
    "VALIDATOR_RELEASE_MODE": "1",
    "VALIDATOR_GUARDRAILS_STRICT": "1",
    "VALIDATOR_SKIP_AUDIO": "1",
}

HERMETIC_PROD_VALIDATOR_ENV = {
    **PROD_LIKE_VALIDATOR_ENV,
    "APP_ENV": "prod",
    "METRO_DB_ENGINE": "postgres",
    "DATABASE_URL": "postgresql://ci:ci@127.0.0.1:5432/metrotherapy_ci_contract",
    "BOT_TOKEN": "000000:CI",
    "ADMIN_IDS": "1",
    "TELEGRAM_TRANSPORT": "polling",
    "TELEGRAM_WEBHOOK_ENABLED": "0",
    "TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED": "0",
    "TOKEN_ECONOMY_ENABLED": "1",
    "TOKEN_ENFORCEMENT_MODE": "hard",
    "PAYMENT_HTTP_ENABLED": "1",
    "PAYMENT_PUBLIC_BASE_URL": "https://metrotherapy.example",
    "PAYMENT_CHECKOUT_SIGNING_KEY": "ci-checkout-signing-key-32-bytes-minimum",
    "PAYMENT_CHECKOUT_INTENT_REQUIRED": "1",
    "YOOKASSA_SHOP_ID": "ci-shop",
    "YOOKASSA_SECRET_KEY": "ci-yookassa-secret-key",
    "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED": "1",
    "YOOKASSA_RECEIPT_EMAIL": "quality-check@metrotherapy.example",
    "YOOKASSA_TAX_SYSTEM_CODE": "2",
    "YOOKASSA_VAT_CODE": "1",
    "YOOKASSA_PAYMENT_MODE": "full_payment",
    "YOOKASSA_PAYMENT_SUBJECT": "service",
    "TELEGRAM_STARS_ENABLED": "1",
    "TELEGRAM_YOOKASSA_ENABLED": "0",
}
'''

OPTIONAL_STEP = '''    GateStep(
        "optional prod-config validation",
        (sys.executable, "scripts/validate_project.py"),
        PROD_LIKE_VALIDATOR_ENV,
        env_file=PROD_ENV_FILE,
        skip_if_missing_env_file=True,
    ),
'''

REQUIRED_AND_OPTIONAL_STEPS = '''    GateStep(
        "hermetic production contract validation",
        (sys.executable, "scripts/validate_project.py"),
        HERMETIC_PROD_VALIDATOR_ENV,
    ),
    GateStep(
        "optional prod-config validation",
        (sys.executable, "scripts/validate_project.py"),
        PROD_LIKE_VALIDATOR_ENV,
        env_file=PROD_ENV_FILE,
        skip_if_missing_env_file=True,
    ),
'''


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        raise SystemExit(f"expected exactly one {label} target, got {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(text, ENV_BLOCK, ENV_BLOCK_WITH_HERMETIC, label="environment block")
    text = replace_once(text, OPTIONAL_STEP, REQUIRED_AND_OPTIONAL_STEPS, label="prod validation step")
    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
