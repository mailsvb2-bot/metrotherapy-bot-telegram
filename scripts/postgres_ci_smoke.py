from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.db import db
from services.db.runtime import CONFIG
from services.payments.telegram_stars import (
    build_stars_payload,
    record_successful_stars_payment,
)
from services.payments.telegram_stars_refunds import (
    cancel_prepared_stars_refund,
    prepare_stars_refund,
    preview_stars_refund,
)
from services.practice_token_contract import package_by_id, telegram_stars_price
from services.practice_tokens import get_wallet
from services.schema import init_db


def _enabled(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _assert_ci_guardrails() -> None:
    if not _enabled("POSTGRES_CI_SMOKE"):
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED explicit POSTGRES_CI_SMOKE=1 is required")
    if (os.getenv("APP_ENV") or "").strip().lower() in {"prod", "production"}:
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED refuses production environment")
    if not CONFIG.uses_postgres:
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED active engine is not Postgres")


def _exercise_payment_and_refund() -> tuple[int, str]:
    suffix = uuid.uuid4().hex
    user_id = 8_000_000_000 + (uuid.uuid4().int % 1_000_000_000)
    charge_id = f"postgres-ci-stars-{suffix}"
    package_id = "practice_start_7"
    package = package_by_id(package_id)
    payload = build_stars_payload(buyer_user_id=user_id, package_id=package_id)
    amount = telegram_stars_price(package_id)

    first = record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=amount,
        currency="XTR",
        telegram_charge_id=charge_id,
    )
    duplicate = record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=amount,
        currency="XTR",
        telegram_charge_id=charge_id,
    )
    if first.duplicate or not duplicate.duplicate:
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED Stars idempotency contract")
    if get_wallet(user_id).available_tokens != package.tokens:
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED token grant contract")

    plan = preview_stars_refund(charge_id)
    if not plan.refundable or plan.tokens != package.tokens:
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED refund preflight contract")
    prepared = prepare_stars_refund(charge_id, requested_by=user_id)
    if prepared.status != "prepared" or get_wallet(user_id).available_tokens != 0:
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED refund hold contract")
    cancel_prepared_stars_refund(charge_id, error="ci_provider_not_called")
    if get_wallet(user_id).available_tokens != package.tokens:
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED refund hold rollback contract")
    return user_id, charge_id


def _assert_ledgers(charge_id: str) -> None:
    with db() as conn:
        payment = conn.execute(
            "SELECT processing_status FROM payments WHERE telegram_charge_id=?",
            (charge_id,),
        ).fetchone()
        refund = conn.execute(
            "SELECT status, attempts FROM telegram_stars_refunds WHERE telegram_charge_id=?",
            (charge_id,),
        ).fetchone()
    if not payment or payment["processing_status"] != "side_effects_done":
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED payment ledger contract")
    if not refund or refund["status"] != "failed" or int(refund["attempts"]) != 1:
        raise SystemExit("POSTGRES_CI_SMOKE_FAILED refund ledger contract")


def main() -> int:
    _assert_ci_guardrails()
    init_db()
    _, charge_id = _exercise_payment_and_refund()
    _assert_ledgers(charge_id)
    print("POSTGRES_CI_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
