from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEBHOOK = ROOT / "ops" / "deploy_webhook.py"
WORKER = ROOT / "scripts" / "run_deploy_worker.sh"


def _load_webhook_module():
    spec = importlib.util.spec_from_file_location("deploy_webhook_contract", WEBHOOK)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deploy_worker_has_valid_bash_syntax_and_cleanup_trap() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    completed = subprocess.run(
        [bash, "-n", str(WORKER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr

    text = WORKER.read_text(encoding="utf-8")
    assert "trap cleanup EXIT INT TERM HUP" in text
    assert "metrotherapy_deploy.lock" in text
    assert '"$DEPLOY_SH" >> "$LOG_FILE" 2>&1' in text
    assert "deploy queued finished" in text


def test_webhook_queues_deploy_as_independent_trigger_bound_systemd_service(monkeypatch) -> None:
    module = _load_webhook_module()
    captured: dict[str, object] = {}
    trigger_sha = "a" * 40

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="1234567890abcdef"),
    )

    module._run_deploy_background(trigger_sha)

    command = captured["command"]
    assert command[0] == "/usr/bin/systemd-run"
    assert command[1:3] == ["--unit", "metrotherapy-deploy-1234567890ab"]
    assert "--collect" in command
    assert "--no-block" in command
    assert "--property=Type=exec" in command
    assert "--property=WorkingDirectory=/root/metrotherapy" in command
    assert f"--setenv=DEPLOY_TRIGGER_SHA={trigger_sha}" in command
    assert command[-2:] == [
        "/usr/bin/bash",
        "/root/metrotherapy/scripts/run_deploy_worker.sh",
    ]
    assert captured["kwargs"] == {
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": 10,
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "0" * 40,
        "a" * 39,
        "a" * 41,
        "g" * 40,
        "../" + "a" * 37,
    ],
)
def test_webhook_rejects_invalid_or_non_commit_trigger_sha(value) -> None:
    module = _load_webhook_module()

    assert module._validated_trigger_sha(value) is None


def test_webhook_normalizes_valid_trigger_sha() -> None:
    module = _load_webhook_module()

    assert module._validated_trigger_sha("A" * 40) == "a" * 40


def test_webhook_maps_systemd_queue_failure_to_one_domain_exception(monkeypatch) -> None:
    module = _load_webhook_module()

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="unit rejected",
        ),
    )

    with pytest.raises(module.DeployQueueError, match="unit rejected"):
        module._run_deploy_background("b" * 40)


def test_webhook_rejects_invalid_sha_before_systemd(monkeypatch) -> None:
    module = _load_webhook_module()
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("systemd-run must not be called")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.DeployQueueError, match="invalid deploy trigger sha"):
        module._run_deploy_background("not-a-sha")
    assert called is False


def test_webhook_no_longer_spawns_deploy_inside_its_own_cgroup() -> None:
    text = WEBHOOK.read_text(encoding="utf-8")

    assert "subprocess.Popen" not in text
    assert "start_new_session" not in text
    assert "/usr/bin/systemd-run" in text
    assert "run_deploy_worker.sh" in text
    assert "DEPLOY_TRIGGER_SHA" in text
    assert 'payload.get("after")' in text
    assert "except DeployQueueError as exc" in text
    assert "deploy queue failed" in text
