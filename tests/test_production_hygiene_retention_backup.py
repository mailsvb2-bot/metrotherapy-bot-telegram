from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import install_postgres_backup_timer, postgres_backup, prune_release_python_cache


def test_release_python_cache_prunes_only_stale_release_names(tmp_path: Path) -> None:
    releases = tmp_path / "runtime" / "releases"
    releases.mkdir(parents=True)
    live_sha = "a" * 40
    stale_sha = "b" * 40
    build_sha = "c" * 40
    (releases / live_sha).mkdir()

    cache_prefix = tmp_path / "cache"
    cache_root = prune_release_python_cache._cache_root(cache_prefix, releases)
    cache_root.mkdir(parents=True)
    for name in (live_sha, stale_sha, f".build-{build_sha}.abc123", "keep-me"):
        path = cache_root / name
        path.mkdir()
        (path / "marker").write_text(name, encoding="utf-8")

    removed = prune_release_python_cache.prune_release_python_cache(
        cache_prefix=cache_prefix,
        releases_dir=releases,
    )

    assert removed == 2
    assert (cache_root / live_sha).is_dir()
    assert not (cache_root / stale_sha).exists()
    assert not (cache_root / f".build-{build_sha}.abc123").exists()
    assert (cache_root / "keep-me").is_dir()


def test_release_python_cache_rejects_relative_roots(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        prune_release_python_cache.prune_release_python_cache(
            cache_prefix=Path("relative-cache"),
            releases_dir=tmp_path / "releases",
        )


def _configure_backup_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    root = tmp_path / "app"
    env_file = tmp_path / "etc" / "metrotherapy.env"
    legacy_cron = tmp_path / "cron.d" / "metrotherapy_pg_backup"
    service = tmp_path / "systemd" / "metrotherapy-postgres-backup.service"
    timer = tmp_path / "systemd" / "metrotherapy-postgres-backup.timer"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("METRO_DB_ENGINE=postgres\n", encoding="utf-8")
    legacy_cron.parent.mkdir(parents=True)
    legacy_cron.write_text("legacy\n", encoding="utf-8")
    service.parent.mkdir(parents=True)

    monkeypatch.setattr(install_postgres_backup_timer, "ROOT", root)
    monkeypatch.setattr(install_postgres_backup_timer, "PYTHON", root / ".venv/bin/python")
    monkeypatch.setattr(install_postgres_backup_timer, "ENV_FILE", env_file)
    monkeypatch.setattr(install_postgres_backup_timer, "LEGACY_CRON", legacy_cron)
    monkeypatch.setattr(install_postgres_backup_timer, "SERVICE", str(service))
    monkeypatch.setattr(install_postgres_backup_timer, "TIMER", str(timer))
    monkeypatch.setattr(install_postgres_backup_timer, "_required_bin", lambda *_args, **_kwargs: "/bin/systemctl")
    return service, timer, legacy_cron


def test_backup_timer_uses_canonical_env_and_removes_legacy_only_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _timer, legacy_cron = _configure_backup_installer(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is True
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install_postgres_backup_timer.subprocess, "run", run)
    install_postgres_backup_timer.install()

    service_text = service.read_text(encoding="utf-8")
    assert "EnvironmentFile=" in service_text
    assert "/etc/metrotherapy/metrotherapy.env" not in service_text
    assert "--env-file" in service_text
    assert calls[-1] == ["/bin/systemctl", "start", "metrotherapy-postgres-backup.service"]
    assert not legacy_cron.exists()


def test_backup_timer_preserves_legacy_cron_when_validation_run_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _service, _timer, legacy_cron = _configure_backup_installer(tmp_path, monkeypatch)

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProces[str]:
        if command[:2] == ["/bin/systemctl", "start"]:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install_postgres_backup_timer.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        install_postgres_backup_timer.install()
    assert legacy_cron.exists()


def test_postgres_backup_prune_counts_legacy_and_canonical_formats(tmp_path: Path) -> None:
    names = [
        "old.sql.gz",
        "middle.dump",
        "new.sql",
        "ignore.txt",
    ]
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        os.utime(path, (index + 1, index + 1))

    postgres_backup.prune_backups(backup_dir=tmp_path, keep=2)

    assert not (tmp_path / "old.sql.gz").exists()
    assert (tmp_path / "middle.dump").exists()
    assert (tmp_path / "new.sql").exists()
    assert (tmp_path / "ignore.txt").exists()


def test_immutable_deploy_cache_cleanup_is_non_critical() -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts/immutable_deploy.sh").read_text(encoding="utf-8")
    cleanup = source.index("cleanup_old_releases")
    cache = source.index("prune_release_python_cache.py", cleanup)
    success = source.index("IMMUTABLE_DEPLOY_OK", cache)
    assert cleanup < cache < success
    assert 'if ! "$SYSTEM_PYTHON"' in source[cleanup:success]
