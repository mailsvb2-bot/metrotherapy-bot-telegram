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


def test_webhook_counts_only_canonical_transient_deploy_units(monkeypatch) -> None:
    module = _load_webhook_module()
    output = "\n".join(
        [
            "metrotherapy-deploy-1234567890ab.service loaded active running worker",
            "metrotherapy-deploy-abcdef123456.service loaded activating start worker",
            "metrotherapy-deploy-fedcba654321.service loaded failed failed worker",
            "unrelated.service loaded active running unrelated",
            "metrotherapy-deploy-NOTHEX.service loaded active running malformed",
        ]
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=output,
            stderr="",
        ),
    )

    assert module._deploy_unit_counts() == (3, 2)


def test_webhook_deploy_status_uses_only_allowlisted_log_markers(tmp_path) -> None:
    module = _load_webhook_module()
    trigger = "a" * 40
    log_file = tmp_path / "deploy.log"
    log_file.write_text(
        "\n".join(
            [
                "VK_GROUP_TOKEN=must-never-leak",
                f"=== deploy trigger sha: {trigger} ===",
                f"=== deploy queued started trigger={trigger}: 2026-07-16T18:00:00+00:00 ===",
                "provider raw response secret=must-never-leak",
                f"=== deploy queued finished trigger={trigger}: 2026-07-16T18:01:00+00:00 ===",
            ]
        ),
        encoding="utf-8",
    )

    status = module._safe_deploy_log_status(log_file)

    assert status["trigger"] == trigger[:12]
    assert status["stage"] == "post_deploy_audit"
    assert status["code"] == "0"
    assert status["updated_at"].endswith("Z")
    assert "must-never-leak" not in repr(status)


def test_webhook_deploy_status_reports_result_publish_failure_without_error_text(
    tmp_path,
) -> None:
    module = _load_webhook_module()
    trigger = "b" * 40
    log_file = tmp_path / "deploy.log"
    log_file.write_text(
        "\n".join(
            [
                f"=== deploy trigger sha: {trigger} ===",
                f"=== deploy queued started trigger={trigger}: now ===",
                f"=== deploy queued finished trigger={trigger}: now ===",
                "ERROR: unable to publish audit result after retries: "
                f"[vk-provider-audit-result] trigger={trigger[:12]} "
                "status=error error=SECRET_VALUE",
            ]
        ),
        encoding="utf-8",
    )

    status = module._safe_deploy_log_status(log_file)

    assert status == {
        "trigger": trigger[:12],
        "stage": "result_publish_error",
        "code": "34",
        "updated_at": status["updated_at"],
    }
    assert "SECRET_VALUE" not in repr(status)


def test_webhook_observability_marks_orphaned_deploy_stage(monkeypatch) -> None:
    module = _load_webhook_module()
    monkeypatch.setattr(module, "_deploy_unit_counts", lambda: (0, 0))
    monkeypatch.setattr(
        module,
        "_safe_deploy_log_status",
        lambda: {
            "trigger": "c" * 12,
            "stage": "deploying",
            "code": "0",
            "updated_at": "2026-07-16T18:00:00Z",
        },
    )

    status = module._deploy_observability()

    assert status["stage"] == "deploy_exited_before_finish"
    assert status["code"] == "unknown"
    assert status["units"] == "0"
    assert status["running"] == "0"


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


def test_webhook_exposes_only_secret_safe_deploy_observability() -> None:
    text = WEBHOOK.read_text(encoding="utf-8")

    assert "deploy_units=" in text
    assert "deploy_running=" in text
    assert "deploy_last_trigger=" in text
    assert "deploy_last_stage=" in text
    assert "deploy_last_code=" in text
    assert "deploy_log_updated_at=" in text
    assert "_LOG_TAIL_BYTES" in text
    assert "Free-form log text is never returned" in text
    assert "DEPLOY_LOG.read_text" not in text
    assert "completed.stderr" not in text[text.index("def _deploy_unit_counts"):]
