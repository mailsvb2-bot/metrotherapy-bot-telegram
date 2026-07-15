from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "run_deploy_worker.sh"
ENV_EXAMPLE = ROOT / "deploy" / "metrotherapy.env.example"


EXPECTED_ENV = {
    "TELEGRAM_STARS_PRICING_MODE": "explicit",
    "TELEGRAM_STARS_PRICE_PRACTICE_START_7": "1500",
    "TELEGRAM_STARS_PRICE_PRACTICE_60": "2500",
    "TELEGRAM_STARS_PRICE_PRACTICE_ANTISTRESS_60": "5000",
    "TELEGRAM_STARS_PRICE_PRACTICE_PERSONAL_MONTH": "15000",
}


def test_deploy_worker_migrates_production_to_explicit_stars_ladder_once() -> None:
    source = WORKER.read_text(encoding="utf-8")

    assert "telegram-stars-explicit-ladder-v1.applied" in source
    assert 'if [ ! -e "$STARS_PRICE_MIGRATION_MARKER" ]' in source
    for key, value in EXPECTED_ENV.items():
        assert f'values["{key}"] = "{value}"' in source
    assert source.index('values["TELEGRAM_STARS_PRICING_MODE"]') < source.index('/usr/bin/bash "$DEPLOY_SH"')
    assert source.index('/usr/bin/bash "$DEPLOY_SH"') < source.index('touch "$STARS_PRICE_MIGRATION_MARKER"')


def test_example_environment_uses_the_same_explicit_ladder() -> None:
    env = ENV_EXAMPLE.read_text(encoding="utf-8")

    for key, value in EXPECTED_ENV.items():
        assert f"{key}={value}" in env
    assert "TELEGRAM_STARS_PRICING_MODE=buyer_parity" not in env


def test_failed_deploy_rolls_back_all_pending_env_migrations() -> None:
    source = WORKER.read_text(encoding="utf-8")

    assert 'if [ "$code" -ne 0 ] && [ "$MIGRATION_PENDING" = "1" ]' in source
    assert 'cp -a "$ENV_BACKUP" "$ENV_FILE" || true' in source
    assert 'STARS_PRICE_MIGRATION_PENDING=1' in source
    assert 'if [ "$STARS_PRICE_MIGRATION_PENDING" = "1" ]' in source
