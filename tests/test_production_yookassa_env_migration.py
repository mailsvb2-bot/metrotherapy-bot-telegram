from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "run_deploy_worker.sh"


def _source() -> str:
    return WORKER.read_text(encoding="utf-8")


def test_deploy_worker_enables_telegram_yookassa_once() -> None:
    source = _source()

    assert "TELEGRAM_YOOKASSA_ENABLED=1" in source
    assert "telegram-yookassa-dual-payment-v1.applied" in source
    assert "if [ ! -e \"$YOOKASSA_MIGRATION_MARKER\" ]" in source
    assert source.index("TELEGRAM_YOOKASSA_ENABLED=1") < source.index('/usr/bin/bash "$DEPLOY_SH"')
    assert source.index('/usr/bin/bash "$DEPLOY_SH"') < source.index('touch "$YOOKASSA_MIGRATION_MARKER"')


def test_deploy_worker_restores_env_when_deploy_fails() -> None:
    source = _source()

    assert 'cp -a "$ENV_FILE" "$ENV_BACKUP"' in source
    assert 'cp -a "$ENV_BACKUP" "$ENV_FILE" || true' in source
    assert 'if [ "$code" -ne 0 ] && [ "$MIGRATION_PENDING" = "1" ]' in source


def test_migration_state_is_outside_git_worktree() -> None:
    source = _source()

    assert "/var/lib/metrotherapy/deploy-migrations" in source
    assert "$APP_DIR/data/deploy/telegram-yookassa-dual-payment-v1.applied" not in source
