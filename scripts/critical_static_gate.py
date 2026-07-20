from __future__ import annotations

import argparse
import subprocess  # nosec B404 - fixed local quality tools without shell
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TYPE_CONTRACT_FILES = (
    "check_db.py",
    "dashboard/sla_dashboard.py",
    "dashboard/sla_retention_money.py",
    "handlers/info.py",
    "runtime/messenger_ingress_reliability.py",
    "runtime/messenger_media_http.py",
    "runtime/messenger_webhooks.py",
    "runtime/payment_http.py",
    "runtime/payment_webhook_admission.py",
    "scripts/all_user_scenario_gate.py",
    "scripts/archive_legacy_sqlite.py",
    "scripts/backup_db.py",
    "scripts/check_deploy_governance.py",
    "scripts/immutable_release.py",
    "scripts/probe_auto_audio_dry_run.py",
    "scripts/probe_payment_reconciliation_live.py",
    "scripts/probe_scheduler_job_live.py",
    "scripts/probe_user_journey_e2e.py",
    "scripts/production_gate.py",
    "scripts/register_max_webhook.py",
    "scripts/restore_db.py",
    "scripts/stress_db.py",
    "scripts/user_scenario_gate.py",
    "services/accounts/identity.py",
    "services/messenger/audio_access.py",
    "services/messenger/delivery_outbox.py",
    "services/messenger/text_ui.py",
    "services/messenger/webhook_dedupe.py",
    "services/payments/checkout_intent.py",
    "services/payments/receipt_contract.py",
    "services/payments/retry_queue.py",
    "services/payments/telegram_stars.py",
    "services/payments/telegram_stars_refunds.py",
    "services/payments/verified_reconciliation.py",
    "services/payments/yookassa_checkout.py",
    "services/payments/yookassa_refunds.py",
    "services/practice_token_lots.py",
    "services/practice_tokens_access_core.py",
    "services/practice_tokens_wallet.py",
    "services/premium_entitlements.py",
    "services/privacy_controls.py",
    "services/probe_safety.py",
    "services/sales_desk.py",
    "services/sales_desk_repository.py",
    "services/sales_desk_sync.py",
)

SECURITY_SCAN_PATHS = (
    "check_db.py",
    "dashboard/sla_dashboard.py",
    "dashboard/sla_retention_money.py",
    "handlers/info.py",
    "runtime/messenger_ingress_reliability.py",
    "runtime/messenger_media_http.py",
    "runtime/messenger_webhooks.py",
    "runtime/payment_http.py",
    "runtime/payment_webhook_admission.py",
    "scripts/all_user_scenario_gate.py",
    "scripts/archive_legacy_sqlite.py",
    "scripts/backup_db.py",
    "scripts/immutable_release.py",
    "scripts/probe_auto_audio_dry_run.py",
    "scripts/probe_payment_reconciliation_live.py",
    "scripts/probe_scheduler_job_live.py",
    "scripts/probe_user_journey_e2e.py",
    "scripts/production_gate.py",
    "scripts/register_max_webhook.py",
    "scripts/restore_db.py",
    "scripts/stress_db.py",
    "scripts/user_scenario_gate.py",
    "services/accounts/identity.py",
    "services/messenger/audio_access.py",
    "services/messenger/delivery_outbox.py",
    "services/messenger/text_ui.py",
    "services/messenger/webhook_dedupe.py",
    "services/payments",
    "services/practice_token_lots.py",
    "services/practice_tokens_access_core.py",
    "services/practice_tokens_wallet.py",
    "services/premium_entitlements.py",
    "services/privacy_controls.py",
    "services/probe_safety.py",
    "services/sales_desk.py",
    "services/sales_desk_db.py",
    "services/sales_desk_repository.py",
    "services/sales_desk_sync.py",
)


def missing_critical_paths() -> list[str]:
    declared = sorted(set(TYPE_CONTRACT_FILES) | set(SECURITY_SCAN_PATHS))
    return [relative for relative in declared if not (ROOT / relative).exists()]


def _run(command: list[str]) -> int:
    proc = subprocess.run(  # nosec B603 - fixed executable and repository-owned path manifest
        command,
        cwd=str(ROOT),
        check=False,
    )
    return int(proc.returncode)


def run_mypy() -> int:
    return _run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--follow-imports=skip",
            "--check-untyped-defs",
            *TYPE_CONTRACT_FILES,
        ]
    )


def run_bandit() -> int:
    return _run(
        [
            sys.executable,
            "-m",
            "bandit",
            "-q",
            "-r",
            "-c",
            "pyproject.toml",
            *SECURITY_SCAN_PATHS,
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the centralized critical static-analysis gate")
    parser.add_argument("check", choices=("manifest", "mypy", "bandit", "all"))
    args = parser.parse_args()

    missing = missing_critical_paths()
    if missing:
        print("CRITICAL_STATIC_MANIFEST_FAILED")
        for relative in missing:
            print(f"missing: {relative}")
        return 2
    print(
        "CRITICAL_STATIC_MANIFEST_OK "
        f"type_files={len(TYPE_CONTRACT_FILES)} security_paths={len(SECURITY_SCAN_PATHS)}"
    )

    if args.check == "manifest":
        return 0
    if args.check in {"mypy", "all"}:
        code = run_mypy()
        if code:
            return code
        print("CRITICAL_MYPY_OK")
    if args.check in {"bandit", "all"}:
        code = run_bandit()
        if code:
            return code
        print("CRITICAL_BANDIT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
