from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "run_deploy_worker.sh"


def _source() -> str:
    return WORKER.read_text(encoding="utf-8")


def test_deploy_worker_disables_telegram_yookassa_once() -> None:
    source = _source()

    assert "telegram-stars-only-checkout-v1.applied" in source
    assert 'if [ ! -e "$STARS_ONLY_MIGRATION_MARKER" ]' in source
    stars_only_block = source.index('if [ ! -e "$STARS_ONLY_MIGRATION_MARKER" ]')
    deploy_call = source.index('/usr/bin/bash "$DEPLOY_SH"')
    assert source.index('print "TELEGRAM_YOOKASSA_ENABLED=0"', stars_only_block) < deploy_call
    assert deploy_call < source.index('touch "$STARS_ONLY_MIGRATION_MARKER"')


def test_historical_enable_migration_is_overridden_before_deploy() -> None:
    source = _source()

    historical = source.index('if [ ! -e "$YOOKASSA_MIGRATION_MARKER" ]')
    stars_only = source.index('if [ ! -e "$STARS_ONLY_MIGRATION_MARKER" ]')
    deploy_call = source.index('/usr/bin/bash "$DEPLOY_SH"')
    assert historical < stars_only < deploy_call
    assert source.index('TELEGRAM_YOOKASSA_ENABLED=1', historical) < stars_only
    assert source.index('TELEGRAM_YOOKASSA_ENABLED=0', stars_only) < deploy_call


def test_deploy_worker_restores_env_when_deploy_fails() -> None:
    source = _source()

    assert 'cp -a "$ENV_FILE" "$ENV_BACKUP"' in source
    assert 'cp -a "$ENV_BACKUP" "$ENV_FILE" || true' in source
    assert 'if [ "$code" -ne 0 ] && [ "$MIGRATION_PENDING" = "1" ]' in source
    assert 'STARS_ONLY_MIGRATION_PENDING=1' in source


def test_migration_state_is_outside_git_worktree() -> None:
    source = _source()

    assert "/var/lib/metrotherapy/deploy-migrations" in source
    assert "$APP_DIR/data/deploy/telegram-stars-only-checkout-v1.applied" not in source
