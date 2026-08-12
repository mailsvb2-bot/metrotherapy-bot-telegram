from __future__ import annotations

"""Provider-backed YooKassa refund drill for issue #138.

The drill is intentionally inert unless a deploy trigger contains the exact
request marker. It uses only YooKassa's documented TEST bank-card number and
aborts before any refund or local drill mutation unless the provider payment
object says ``test=true``. Real card data is never accepted.

The operator proves three independent flows against the deployed production
webhook and the authoritative production database, while isolating all local
entitlements under reserved negative probe identities:

* premium package -> full refund -> exact lot/wallet/premium revocation + replay;
* starter package -> provider partial refund -> manual partial state, no token guess;
* starter package -> reserve one token -> full refund -> action-required debt state.

Only redacted identifiers and boolean/status evidence are published to GitHub.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_DIR = Path(os.getenv("APP_DIR", str(ROOT)))
ENV_FILE = Path(os.getenv("ENV_FILE", "/etc/metrotherapy/metrotherapy.env"))
DEPLOY_STATE_DIR = Path(os.getenv("DEPLOY_STATE_DIR", "/var/lib/metrotherapy/deploy-state"))
DEPLOYED_SHA_FILE = Path(
    os.getenv("DEPLOYED_SHA_FILE", str(DEPLOY_STATE_DIR / "deployed_sha"))
)
TRIGGER_SHA = (os.getenv("DEPLOY_TRIGGER_SHA") or "").strip().lower()
PUBLIC_WEBHOOK = os.getenv(
    "YOOKASSA_DRILL_WEBHOOK_URL",
    "https://metrotherapy-bot.metrotherapy.ru/pay/yookassa/webhook",
).strip()
REQUEST_MARKER = "[yookassa-refund-live-proof-request]"
RESULT_MARKER = "[ops-live-proof-result]"

_API_BASE = "https://api.yookassa.ru/v3"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_RESULT_RE = re.compile(r"[^A-Za-z0-9_.:=,/\[\] -]+")
_TEST_CARD_NUMBER = "".join(("5555", "5555", "5555", "4444"))
_TEST_CARD_HOLDER = "METROTHERAPY TEST"
_TEST_CARD_EXPIRY_MONTH = "12"
_TEST_CARD_EXPIRY_YEAR = "2030"
_TEST_CARD_CSC = "123"

_FULL_USER_ID = -9_138_001
_PARTIAL_USER_ID = -9_138_002
_RESERVED_USER_ID = -9_138_003
_FULL_PACKAGE = "practice_antistress_60"
_PARTIAL_PACKAGE = "practice_start_7"
_RESERVED_PACKAGE = "practice_start_7"
_PARTIAL_REFUND_VALUE = "1000.00"


class RefundDrillError(RuntimeError):
    """Expected fail-closed drill error safe to reduce to a short status code."""


def _run(
    args: list[str],
    *,
    check: bool = True,
    timeout: int = 120,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise RefundDrillError(f"command_timeout:{Path(args[0]).name}") from exc
    except OSError as exc:
        raise RefundDrillError(f"command_os_error:{Path(args[0]).name}") from exc
    if check and completed.returncode != 0:
        raise RefundDrillError(
            f"command_failed:{Path(args[0]).name}:{completed.returncode}"
        )
    return completed


def _safe_fragment(value: object, *, limit: int = 180) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return (_SAFE_RESULT_RE.sub("_", text)[:limit] or "NONE").strip()


def _redacted_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "NONE"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    suffix = re.sub(r"[^A-Za-z0-9]", "", raw)[-6:] or "id"
    return f"sha256:{digest}:..{suffix}"


def _trigger_message() -> str:
    if _SHA_RE.fullmatch(TRIGGER_SHA) is None:
        raise RefundDrillError("invalid_trigger_sha")
    _run(["/usr/bin/git", "-C", str(APP_DIR), "fetch", "origin", "main"], timeout=180)
    return _run(
        ["/usr/bin/git", "-C", str(APP_DIR), "show", "-s", "--format=%B", TRIGGER_SHA],
        timeout=30,
    ).stdout


def _require_trigger() -> None:
    message = _trigger_message()
    if REQUEST_MARKER not in message:
        raise RefundDrillError("request_marker_missing")
    if RESULT_MARKER in message:
        raise RefundDrillError("result_commit_is_not_request")
    try:
        deployed = DEPLOYED_SHA_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError as exc:
        raise RefundDrillError("deployed_sha_unreadable") from exc
    if deployed != TRIGGER_SHA:
        raise RefundDrillError("trigger_not_exact_deployed_sha")


def _publish_result(message: str) -> None:
    safe_message = _safe_fragment(message, limit=900)
    for attempt in range(1, 4):
        _run(["/usr/bin/git", "-C", str(APP_DIR), "fetch", "origin", "main"], timeout=180)
        parent = _run(
            ["/usr/bin/git", "-C", str(APP_DIR), "rev-parse", "origin/main"], timeout=20
        ).stdout.strip()
        tree = _run(
            ["/usr/bin/git", "-C", str(APP_DIR), "rev-parse", f"{parent}^{{tree}}"],
            timeout=20,
        ).stdout.strip()
        commit = _run(
            [
                "/usr/bin/git",
                "-C",
                str(APP_DIR),
                "-c",
                "user.name=Metrotherapy Refund Drill",
                "-c",
                "user.email=refund-drill@metrotherapy.local",
                "commit-tree",
                tree,
                "-p",
                parent,
                "-F",
                "-",
            ],
            timeout=20,
            input_text=safe_message + "\n",
        ).stdout.strip()
        pushed = _run(
            [
                "/usr/bin/git",
                "-C",
                str(APP_DIR),
                "push",
                "origin",
                f"{commit}:refs/heads/main",
            ],
            check=False,
            timeout=180,
        )
        if pushed.returncode == 0:
            return
        time.sleep(attempt)
    raise RefundDrillError("result_publish_race")


def _load_env(names: tuple[str, ...]) -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise RefundDrillError("env_file_missing")
    fmt = "".join("%s\\0" for _ in names)
    values = " ".join(f'"${{{name}:-}}"' for name in names)
    command = f'set -a; . "$1"; set +a; printf \'{fmt}\' {values}'
    try:
        raw = subprocess.run(
            ["/usr/bin/bash", "-c", command, "bash", str(ENV_FILE)],
            check=False,
            capture_output=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise RefundDrillError("env_source_timeout") from exc
    except OSError as exc:
        raise RefundDrillError("env_source_os_error") from exc
    if raw.returncode != 0:
        raise RefundDrillError("env_source_failed")
    parts = raw.stdout.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) != len(names):
        raise RefundDrillError("env_source_shape_invalid")
    try:
        return {name: parts[index].decode("utf-8") for index, name in enumerate(names)}
    except UnicodeDecodeError as exc:
        raise RefundDrillError("env_source_encoding_invalid") from exc


def _prepare_environment() -> None:
    names = (
        "APP_ENV",
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "YOOKASSA_RECEIPT_EMAIL",
        "PAYMENT_RECEIPT_EMAIL",
        "ADMIN_EMAIL",
        "YOOKASSA_TAX_SYSTEM_CODE",
        "YOOKASSA_VAT_CODE",
        "YOOKASSA_PAYMENT_SUBJECT",
        "YOOKASSA_PAYMENT_MODE",
        "YOOKASSA_PROVIDER_VERIFICATION_REQUIRED",
        "DATABASE_URL",
        "METRO_DB_ENGINE",
    )
    selected = _load_env(names)
    for name, value in selected.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
    os.environ["LOAD_DOTENV"] = "0"
    if (selected.get("APP_ENV") or "").strip().lower() not in {"prod", "production"}:
        raise RefundDrillError("authoritative_env_not_production")
    if not selected.get("YOOKASSA_SHOP_ID") or not selected.get("YOOKASSA_SECRET_KEY"):
        raise RefundDrillError("yookassa_credentials_missing")
    if (selected.get("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RefundDrillError("provider_verification_not_required")


def _auth_header() -> str:
    shop_id = (os.getenv("YOOKASSA_SHOP_ID") or "").strip()
    secret = (os.getenv("YOOKASSA_SECRET_KEY") or "").strip()
    if not shop_id or not secret:
        raise RefundDrillError("yookassa_credentials_missing")
    encoded = base64.b64encode(f"{shop_id}:{secret}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _provider_request(
    path: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    idempotence_key: str | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": _auth_header(), "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if idempotence_key:
        headers["Idempotence-Key"] = idempotence_key[:128]
    request = urllib.request.Request(
        f"{_API_BASE}/{path.lstrip('/')}",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # Provider response bodies can contain operational data. Never echo them.
        raise RefundDrillError(f"provider_http_{exc.code}:{path.split('/', 1)[0]}") from exc
    except urllib.error.URLError as exc:
        raise RefundDrillError(f"provider_url_error:{path.split('/', 1)[0]}") from exc
    except TimeoutError as exc:
        raise RefundDrillError(f"provider_timeout:{path.split('/', 1)[0]}") from exc
    except OSError as exc:
        raise RefundDrillError(f"provider_os_error:{path.split('/', 1)[0]}") from exc
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RefundDrillError("provider_bad_json") from exc
    if not isinstance(data, dict):
        raise RefundDrillError("provider_bad_payload")
    return data


def _amount_value(package_id: str) -> tuple[str, int]:
    from services.practice_token_contract import package_by_id

    package = package_by_id(package_id)
    return f"{int(package.price_rub)}.00", int(package.tokens)


def _payment_payload(*, user_id: int, package_id: str) -> dict[str, Any]:
    from services.payments.yookassa_checkout import build_yookassa_receipt

    amount, tokens = _amount_value(package_id)
    metadata = {
        "project": "metrotherapy",
        "source": "vk",
        "external_user_id": str(user_id),
        "user_id": str(user_id),
        "messenger_external_user_id": str(user_id),
        "kind": "tokens",
        "package_id": package_id,
        "tokens": str(tokens),
        "intent_id": f"refund_drill_{uuid.uuid4().hex}",
    }
    description = f"Metrotherapy refund drill {package_id}"
    return {
        "amount": {"value": amount, "currency": "RUB"},
        "capture": True,
        "description": description[:128],
        "payment_method_data": {
            "type": "bank_card",
            "card": {
                "cardholder": _TEST_CARD_HOLDER,
                "csc": _TEST_CARD_CSC,
                "expiry_month": _TEST_CARD_EXPIRY_MONTH,
                "expiry_year": _TEST_CARD_EXPIRY_YEAR,
                "number": _TEST_CARD_NUMBER,
            },
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://metrotherapy.ru",
        },
        "metadata": metadata,
        "receipt": build_yookassa_receipt(amount_value=amount, description=description),
    }


def _create_test_payment(*, user_id: int, package_id: str) -> dict[str, Any]:
    created = _provider_request(
        "payments",
        method="POST",
        payload=_payment_payload(user_id=user_id, package_id=package_id),
        idempotence_key=f"metrotherapy-refund-drill-payment-{uuid.uuid4()}",
    )
    # This is the hard safety boundary. A real-shop object can never advance to
    # refund/local drill logic, even if someone accidentally points this script at
    # production merchant credentials.
    if created.get("test") is not True:
        raise RefundDrillError("test_shop_required")
    payment_id = str(created.get("id") or "").strip()
    if not payment_id:
        raise RefundDrillError("test_payment_id_missing")
    return _wait_provider_payment(payment_id)


def _wait_until(
    probe: Callable[[], Any],
    accept: Callable[[Any], bool],
    *,
    timeout_seconds: int,
    error_code: str,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if accept(last):
            return last
        time.sleep(2)
    raise RefundDrillError(error_code)


def _wait_provider_payment(payment_id: str) -> dict[str, Any]:
    from services.payments.yookassa_provider import fetch_yookassa_payment

    def probe() -> dict[str, Any]:
        return fetch_yookassa_payment(payment_id)

    payment = _wait_until(
        probe,
        lambda item: str(item.get("status") or "").lower() in {"succeeded", "canceled"},
        timeout_seconds=90,
        error_code="test_payment_provider_timeout",
    )
    if payment.get("test") is not True:
        raise RefundDrillError("provider_payment_not_test")
    if str(payment.get("status") or "").lower() != "succeeded":
        raise RefundDrillError("test_payment_not_succeeded")
    if payment.get("paid") is not True or payment.get("refundable") is not True:
        raise RefundDrillError("test_payment_not_refundable")
    return payment


def _create_refund(payment: dict[str, Any], value: str) -> dict[str, Any]:
    payment_id = str(payment.get("id") or "").strip()
    refund = _provider_request(
        "refunds",
        method="POST",
        payload={
            "payment_id": payment_id,
            "amount": {"value": value, "currency": "RUB"},
        },
        idempotence_key=f"metrotherapy-refund-drill-refund-{uuid.uuid4()}",
    )
    refund_id = str(refund.get("id") or "").strip()
    if not refund_id:
        raise RefundDrillError("refund_id_missing")
    return _wait_provider_refund(refund_id, payment_id)


def _wait_provider_refund(refund_id: str, payment_id: str) -> dict[str, Any]:
    from services.payments.yookassa_provider import fetch_yookassa_refund

    refund = _wait_until(
        lambda: fetch_yookassa_refund(refund_id),
        lambda item: str(item.get("status") or "").lower() in {"succeeded", "canceled"},
        timeout_seconds=90,
        error_code="refund_provider_timeout",
    )
    if str(refund.get("payment_id") or "").strip() != payment_id:
        raise RefundDrillError("refund_payment_mismatch")
    if str(refund.get("status") or "").lower() != "succeeded":
        raise RefundDrillError("refund_not_succeeded")
    return refund


def _row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    from services.db import db

    with db() as conn:
        item = conn.execute(sql, params).fetchone()
    if item is None:
        return {}
    if hasattr(item, "keys"):
        return {str(key): item[key] for key in item.keys()}
    raise RefundDrillError("db_row_shape_invalid")


def _payment_state(payment_id: str) -> dict[str, Any]:
    return _row(
        """
        SELECT user_id, provider_status, processing_status, problem, amount, currency
        FROM payments
        WHERE provider_charge_id=? OR telegram_charge_id=?
        LIMIT 1
        """.strip(),
        (payment_id, f"yookassa:{payment_id}"),
    )


def _lot_state(payment_id: str) -> dict[str, Any]:
    return _row(
        """
        SELECT user_id, package_id, granted_tokens, available_tokens, reserved_tokens,
               used_tokens, refund_held_tokens, refunded_tokens, refundable
        FROM practice_token_lots
        WHERE provider='yookassa' AND provider_payment_id=? LIMIT 1
        """.strip(),
        (payment_id,),
    )


def _wallet_state(user_id: int) -> dict[str, Any]:
    return _row(
        """
        SELECT available_tokens, reserved_tokens, refunded_tokens
        FROM practice_wallets WHERE user_id=? LIMIT 1
        """.strip(),
        (int(user_id),),
    )


def _refund_state(refund_id: str) -> dict[str, Any]:
    return _row(
        """
        SELECT status, problem, amount_minor, cumulative_refunded_minor,
               payment_amount_minor, tokens_affected, debt_tokens
        FROM yookassa_refunds WHERE refund_id=? LIMIT 1
        """.strip(),
        (refund_id,),
    )


def _count(sql: str, params: tuple[Any, ...] = ()) -> int:
    row = _row(sql, params)
    return int(row.get("n") or 0)


def _wait_payment_webhook(payment: dict[str, Any], *, expected_tokens: int) -> None:
    payment_id = str(payment.get("id") or "")

    def probe() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return (
            _payment_state(payment_id),
            _lot_state(payment_id),
            _wallet_state(int(payment.get("metadata", {}).get("user_id") or 0)),
        )

    state, lot, wallet = _wait_until(
        probe,
        lambda item: (
            str(item[0].get("processing_status") or "") == "side_effects_done"
            and int(item[1].get("granted_tokens") or 0) == expected_tokens
            and int(item[2].get("available_tokens") or 0) >= expected_tokens
        ),
        timeout_seconds=120,
        error_code="payment_succeeded_webhook_not_observed",
    )
    if int(state.get("user_id") or 0) >= 0:
        raise RefundDrillError("probe_user_not_reserved_negative")
    if int(lot.get("available_tokens") or 0) != expected_tokens:
        raise RefundDrillError("payment_lot_not_exact")


def _wait_refund_state(refund_id: str, expected_status: str) -> dict[str, Any]:
    return _wait_until(
        lambda: _refund_state(refund_id),
        lambda item: str(item.get("status") or "") == expected_status,
        timeout_seconds=120,
        error_code=f"refund_webhook_not_observed:{expected_status}",
    )


def _post_replay(refund: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(
        {"type": "notification", "event": "refund.succeeded", "object": refund},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        PUBLIC_WEBHOOK,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = int(response.status)
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RefundDrillError(f"replay_http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RefundDrillError("replay_url_error") from exc
    except TimeoutError as exc:
        raise RefundDrillError("replay_timeout") from exc
    except OSError as exc:
        raise RefundDrillError("replay_os_error") from exc
    if status != 200:
        raise RefundDrillError(f"replay_http_{status}")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RefundDrillError("replay_bad_json") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RefundDrillError("replay_not_accepted")
    return payload


def _assert_full_refund(payment: dict[str, Any], refund: dict[str, Any]) -> str:
    payment_id = str(payment.get("id") or "")
    refund_id = str(refund.get("id") or "")
    state = _wait_refund_state(refund_id, "completed")
    lot = _lot_state(payment_id)
    wallet = _wallet_state(_FULL_USER_ID)
    payment_row = _payment_state(payment_id)
    if str(payment_row.get("processing_status") or "") != "refunded":
        raise RefundDrillError("full_payment_not_refunded")
    if int(lot.get("available_tokens") or -1) != 0 or int(lot.get("refunded_tokens") or 0) != 60:
        raise RefundDrillError("full_lot_not_revoked")
    if int(wallet.get("available_tokens") or -1) != 0 or int(wallet.get("refunded_tokens") or 0) < 60:
        raise RefundDrillError("full_wallet_not_revoked")
    if int(state.get("tokens_affected") or 0) != 60 or int(state.get("debt_tokens") or 0) != 0:
        raise RefundDrillError("full_refund_state_invalid")

    entitlement_active = _count(
        "SELECT COUNT(*) AS n FROM premium_entitlements WHERE provider='yookassa' AND provider_payment_id=? AND status!='revoked'",
        (payment_id,),
    )
    outbox_uncancelled = _count(
        "SELECT COUNT(*) AS n FROM premium_delivery_outbox WHERE idempotency_key LIKE ? AND status!='cancelled'",
        (f"premium_delivery:yookassa:{payment_id}:%",),
    )
    entitlement_total = _count(
        "SELECT COUNT(*) AS n FROM premium_entitlements WHERE provider='yookassa' AND provider_payment_id=?",
        (payment_id,),
    )
    outbox_total = _count(
        "SELECT COUNT(*) AS n FROM premium_delivery_outbox WHERE idempotency_key LIKE ?",
        (f"premium_delivery:yookassa:{payment_id}:%",),
    )
    if entitlement_total < 1 or outbox_total < 1 or entitlement_active or outbox_uncancelled:
        raise RefundDrillError("pending_premium_not_revoked")

    ledger_before = _count(
        "SELECT COUNT(*) AS n FROM practice_ledger WHERE idempotency_key=?",
        (f"yookassa_refund_finalize:{payment_id}",),
    )
    before = (_lot_state(payment_id), _wallet_state(_FULL_USER_ID), _payment_state(payment_id))
    replay = _post_replay(refund)
    after = (_lot_state(payment_id), _wallet_state(_FULL_USER_ID), _payment_state(payment_id))
    ledger_after = _count(
        "SELECT COUNT(*) AS n FROM practice_ledger WHERE idempotency_key=?",
        (f"yookassa_refund_finalize:{payment_id}",),
    )
    if replay.get("inserted") is not False or before != after or ledger_before != 1 or ledger_after != 1:
        raise RefundDrillError("refund_replay_mutated_state")
    return (
        f"full=ok payment={_redacted_id(payment_id)} refund={_redacted_id(refund_id)} "
        f"lot=revoked wallet=revoked premium=revoked replay=idempotent"
    )


def _run_full_scenario() -> str:
    amount, tokens = _amount_value(_FULL_PACKAGE)
    payment = _create_test_payment(user_id=_FULL_USER_ID, package_id=_FULL_PACKAGE)
    _wait_payment_webhook(payment, expected_tokens=tokens)
    payment_id = str(payment.get("id") or "")
    if _count(
        "SELECT COUNT(*) AS n FROM premium_entitlements WHERE provider='yookassa' AND provider_payment_id=?",
        (payment_id,),
    ) < 1:
        raise RefundDrillError("premium_entitlement_not_granted")
    if _count(
        "SELECT COUNT(*) AS n FROM premium_delivery_outbox WHERE idempotency_key LIKE ? AND status='sent'",
        (f"premium_delivery:yookassa:{payment_id}:%",),
    ):
        raise RefundDrillError("premium_delivery_raced_to_sent")
    refund = _create_refund(payment, amount)
    return _assert_full_refund(payment, refund)


def _run_partial_scenario() -> str:
    _amount, tokens = _amount_value(_PARTIAL_PACKAGE)
    payment = _create_test_payment(user_id=_PARTIAL_USER_ID, package_id=_PARTIAL_PACKAGE)
    _wait_payment_webhook(payment, expected_tokens=tokens)
    payment_id = str(payment.get("id") or "")
    wallet_before = _wallet_state(_PARTIAL_USER_ID)
    lot_before = _lot_state(payment_id)
    refund = _create_refund(payment, _PARTIAL_REFUND_VALUE)
    refund_id = str(refund.get("id") or "")
    state = _wait_refund_state(refund_id, "partial_recorded")
    wallet_after = _wallet_state(_PARTIAL_USER_ID)
    lot_after = _lot_state(payment_id)
    payment_row = _payment_state(payment_id)
    if str(payment_row.get("processing_status") or "") != "refund_partial_recorded":
        raise RefundDrillError("partial_processing_status_invalid")
    if str(state.get("problem") or "") != "partial_refund_requires_manual_policy":
        raise RefundDrillError("partial_problem_invalid")
    if wallet_before != wallet_after or lot_before != lot_after:
        raise RefundDrillError("partial_refund_guessed_token_ratio")
    return (
        f"partial=ok payment={_redacted_id(payment_id)} refund={_redacted_id(refund_id)} "
        f"tokens=unchanged processing=refund_partial_recorded"
    )


def _reserve_one_probe_token(user_id: int, payment_id: str) -> None:
    from services.db import db, tx
    from services.practice_token_lots import reserve_from_lots

    reservation_id = f"refund-drill-{uuid.uuid4().hex}"
    with db() as conn:
        with tx(conn):
            lot = conn.execute(
                """
                SELECT available_tokens FROM practice_token_lots
                WHERE provider='yookassa' AND provider_payment_id=? AND user_id=? LIMIT 1
                """.strip(),
                (payment_id, int(user_id)),
            ).fetchone()
            if lot is None or int(lot["available_tokens"]) < 1:
                raise RefundDrillError("reserved_probe_lot_missing")
            reserve_from_lots(
                conn,
                user_id=int(user_id),
                reservation_id=reservation_id,
                amount=1,
            )


def _run_reserved_scenario() -> str:
    amount, tokens = _amount_value(_RESERVED_PACKAGE)
    payment = _create_test_payment(user_id=_RESERVED_USER_ID, package_id=_RESERVED_PACKAGE)
    _wait_payment_webhook(payment, expected_tokens=tokens)
    payment_id = str(payment.get("id") or "")
    _reserve_one_probe_token(_RESERVED_USER_ID, payment_id)
    lot = _lot_state(payment_id)
    if int(lot.get("reserved_tokens") or 0) != 1:
        raise RefundDrillError("reserved_probe_not_reserved")
    refund = _create_refund(payment, amount)
    refund_id = str(refund.get("id") or "")
    state = _wait_refund_state(refund_id, "action_required")
    payment_row = _payment_state(payment_id)
    if str(payment_row.get("processing_status") or "") != "refund_action_required":
        raise RefundDrillError("reserved_processing_status_invalid")
    if str(state.get("problem") or "") != "purchased_practices_already_used_or_reserved":
        raise RefundDrillError("reserved_problem_invalid")
    if int(state.get("debt_tokens") or 0) < 1:
        raise RefundDrillError("reserved_debt_missing")
    return (
        f"reserved=ok payment={_redacted_id(payment_id)} refund={_redacted_id(refund_id)} "
        f"processing=refund_action_required debt={int(state.get('debt_tokens') or 0)}"
    )


def run_drill() -> str:
    _require_trigger()
    _prepare_environment()
    full = _run_full_scenario()
    partial = _run_partial_scenario()
    reserved = _run_reserved_scenario()
    return (
        f"{RESULT_MARKER} trigger={TRIGGER_SHA[:12]} status=ok yookassa_refund=ok "
        f"provider_test=true provider_get_refund=ok webhook_refund_succeeded=observed "
        f"deployed={TRIGGER_SHA} {full} {partial} {reserved}"
    )


def main() -> int:
    try:
        result = run_drill()
    except RefundDrillError as exc:
        reason = _safe_fragment(str(exc), limit=160)
        _publish_result(
            f"{RESULT_MARKER} trigger={TRIGGER_SHA[:12] or 'NONE'} status=blocked "
            f"yookassa_refund=blocked reason={reason}"
        )
        return 2
    _publish_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
