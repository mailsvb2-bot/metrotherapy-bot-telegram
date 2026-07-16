from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "run_deploy_worker.sh"


def _source() -> str:
    return WORKER.read_text(encoding="utf-8")


def test_deploy_worker_uses_kernel_flock_instead_of_stale_file_sentinel() -> None:
    source = _source()

    assert 'exec 9<>"$LOCK_FILE"' in source
    assert 'exec 9>"$LOCK_FILE"' not in source
    assert '"$FLOCK_BIN" -w "$LOCK_WAIT_SECONDS" 9' in source
    assert '"$FLOCK_BIN" -u 9 || true' in source
    assert '"$FLOCK_BIN" -n 9' not in source
    assert 'if [ -e "$LOCK_FILE" ]' not in source
    assert 'touch "$LOCK_FILE"' not in source
    assert 'rm -f "$LOCK_FILE"' not in source


def test_deploy_lock_is_acquired_before_metadata_and_production_mutation() -> None:
    source = _source()

    acquire = source.index('"$FLOCK_BIN" -w "$LOCK_WAIT_SECONDS" 9')
    acquisition_epoch = source.index('LOCK_ACQUIRED_EPOCH="$(date +%s)"')
    replace_metadata = source.index(': > "$LOCK_FILE"', acquisition_epoch)
    env_migration = source.index('mkdir -p "$MIGRATION_DIR"')
    deploy = source.index('/usr/bin/bash "$DEPLOY_SH"')

    assert acquire < acquisition_epoch < replace_metadata < env_migration < deploy


def test_persistent_lock_file_is_documented_as_inode_not_sentinel_state() -> None:
    source = _source()

    assert "The file is only a stable inode for the kernel lock" in source
    assert "Waiting workers open it read/write without truncation" in source
    assert "The kernel lock is released automatically on every process exit" in source
    assert "deploy waiting for flock" in source
    assert "deploy lock wait timed out" in source
