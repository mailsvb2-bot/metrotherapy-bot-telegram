from __future__ import annotations

import os
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any

from services.accounts.identity import ensure_account
from services.db import db, tx
from services.practice_token_contract import (
    PracticePackage,
    daily_practice_cost,
    normalize_delivery_mode,
    package_by_id,
    public_practice_packages,
)

_REQUIRED_SCHEMA_TABLES = frozenset({
    "practice_wallets",
    "practice_ledger",
    "payment_token_grants",
    "user_practice_preferences",
    "practice_reservations",
})

EMPTY_BALANCE_MESSAGE = (
    "🔐 На балансе нет доступных практик. "
    "Откройте «💳 Пакеты практик», чтобы продолжить маршрут."
)
RESERVE_FAILED_MESSAGE = "⚠️ Не удалось зарезервировать практику. Попробуйте ещё раз."


@dataclass(frozen=True)
class PracticeWallet:
    user_id: int
    available_tokens: int = 0
    reserved_tokens: int = 0
    used_tokens: int = 0


@dataclass(frozen=True)
class PracticeAccessDecision:
    allowed: bool
    mode: str
    reason: str
    reservation_id: str | None = None
    message: str = ""
    warning: str = ""


def token_economy_enabled() -> bool:
    raw = (os.getenv("TOKEN_ECONOMY_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def enforcement_mode() -> str:
    raw = os.getenv("TOKEN_ENFORCEMENT_MODE")
    if raw is None or not raw.strip():
        app_env = (os.getenv("APP_ENV") or "dev").strip().lower()
        return "soft" if app_env in {"prod", "production"} else "off"

    normalized = raw.strip().lower()
    if normalized in {"hard", "1", "true", "on"}:
        return "hard"
    if normalized in {"soft", "warn", "warning"}:
        return "soft"
    return "off"


def token_access_authoritative() -> bool:
    return token_economy_enabled() and enforcement_mode() != "off"


def ensure_schema(conn: Any) -> None:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'",
    ).fetchall()
    existing = {str(row["name"] if hasattr(row, "keys") else row[0]) for row in rows}
    missing = sorted(_REQUIRED_SCHEMA_TABLES - existing)
    if missing:
        raise RuntimeError(f"practice_token_schema_not_migrated:{','.join(missing)}")


def get_active_packages() -> tuple[PracticePackage, ...]:
    return public_practice_packages()


def get_package(package_id: str | None) -> PracticePackage:
    return package_by_id(package_id)


def _canonical_practice_user_id(user_id: int) -> int:
    uid = int(user_id)
    external = str(uid)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT account_id
            FROM account_channel_identities
            WHERE external_user_id=?
            ORDER BY account_id
            """.strip(),
            (external,),
        ).fetchall()
        account_ids = [int(row["account_id"]) for row in rows]
        if len(account_ids) == 1:
            return account_ids[0]

        row = conn.execute(
            "SELECT account_id FROM accounts WHERE account_id=? LIMIT 1",
            (uid,),
        ).fetchone()
        if row is not None:
            return int(row["account_id"])

    return ensure_account(uid)


def canonical_practice_user_id(user_id: int) -> int:
    """Public canonical account id used by the practice wallet."""

    return _canonical_practice_user_id(int(user_id))


def _wallet_from_row(row: Any, user_id: int) -> PracticeWallet:
    if not row:
        return PracticeWallet(int(user_id), 0, 0, 0)
    return PracticeWallet(
        int(row["user_id"]),
        int(row["available_tokens"]),
        int(row["reserved_tokens"]),
        int(row["used_tokens"]),
    )


def _ensure_wallet(conn: Any, user_id: int) -> None:
    ensure_schema(conn)
    conn.execute(
        "INSERT OR IGNORE INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
        (int(user_id), 0, 0, 0),
    )


def _get_wallet_in_conn(conn: Any, user_id: int) -> PracticeWallet:
    row = conn.execute(
        "SELECT user_id, available_tokens, reserved_tokens, used_tokens FROM practice_wallets WHERE user_id=?",
        (int(user_id),),
    ).fetchone()
    return _wallet_from_row(row, int(user_id))


def _delivered_reservation_ids(user_id: int) -> list[str]:
    """Return reserved rows with durable proof that audio already left the system.

    Session-bound deliveries are proven by mood_sessions.audio_sent. Direct queue
    deliveries are proven by account_audio_progress, which is account-scoped and
    therefore survives a Telegram -> VK/MAX switch.
    """

    with db() as conn:
        ensure_schema(conn)
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT r.reservation_id
                FROM practice_reservations r
                LEFT JOIN mood_sessions s ON s.id=r.session_id
                LEFT JOIN account_audio_progress ap
                  ON ap.account_id=r.user_id
                 AND ap.product_id='metrotherapy'
                 AND ap.program_id='full_series'
                 AND ap.pending_audio_no=r.audio_anchor
                WHERE r.user_id=?
                  AND r.status='reserved'
                  AND (
                        (r.session_id IS NOT NULL AND COALESCE(s.audio_sent,0)=1)
                     OR (r.session_id IS NULL AND r.audio_anchor IS NOT NULL AND ap.pending_audio_no IS NOT NULL)
                  )
                ORDER BY r.created_at, r.reservation_id
                """.strip(),
                (int(user_id),),
            ).fetchall()
        except Exception as exc:  # validator: allow-wide-except
            error_text = str(exc).lower()
            if "does not exist" not in error_text and "no such table" not in error_text:
                raise
            rows = conn.execute(
                """
                SELECT DISTINCT r.reservation_id
                FROM practice_reservations r
                JOIN mood_sessions s ON s.id=r.session_id
                WHERE r.user_id=?
                  AND r.status='reserved'
                  AND COALESCE(s.audio_sent,0)=1
                ORDER BY r.created_at, r.reservation_id
                """.strip(),
                (int(user_id),),
            ).fetchall()
    return [str(row["reservation_id"]) for row in rows]


def reconcile_delivered_reservations(user_id: int) -> int:
    """Repair reservations after a successful external send but failed DB finalize."""

    uid = _canonical_practice_user_id(int(user_id))
    repaired = 0
    for reservation_id in _delivered_reservation_ids(uid):
        if consume_reservation(reservation_id, reason="audio_delivery_reconciled"):
            repaired += 1
    return repaired


def get_wallet(user_id: int) -> PracticeWallet:
    uid = _canonical_practice_user_id(int(user_id))
    reconcile_delivered_reservations(uid)
    with db() as conn:
        _ensure_wallet(conn, uid)
        return _get_wallet_in_conn(conn, uid)


def has_paid_practice_access(user_id: int) -> bool:
    """Whether the canonical token product currently has usable/in-flight access."""

    wallet = get_wallet(int(user_id))
    return int(wallet.available_tokens) > 0 or int(wallet.reserved_tokens) > 0


def _insert_ledger(
    conn: Any,
    *,
    user_id: int,
    event_type: str,
    amount: int,
    balance_after: int,
    reason: str,
    source: str = "",
    package_id: str | None = None,
    provider: str | None = None,
    provider_payment_id: str | None = None,
    idempotency_key: str,
) -> int:
    conn.execute(
        """
        INSERT INTO practice_ledger(
            user_id, event_type, amount, balance_after, reason, source,
            package_id, provider, provider_payment_id, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """.strip(),
        (
            int(user_id), event_type, int(amount), int(balance_after), reason,
            source, package_id, provider, provider_payment_id, idempotency_key,
        ),
    )
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"] if row else 0)


def grant_tokens(
    user_id: int,
    *,
    package_id: str,
    amount: int,
    provider: str = "manual",
    provider_payment_id: str = "",
    source: str = "",
    idempotency_key: str | None = None,
) -> tuple[bool, PracticeWallet, int | None]:
    uid = _canonical_practice_user_id(int(user_id))
    idempotency_key = idempotency_key or f"grant:{provider}:{provider_payment_id}:{uid}:{package_id}:{amount}"
    with db() as conn:
        with tx(conn):
            _ensure_wallet(conn, uid)
            existing = conn.execute(
                "SELECT id FROM practice_ledger WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return False, _get_wallet_in_conn(conn, uid), int(existing["id"])
            wallet = _get_wallet_in_conn(conn, uid)
            balance = int(wallet.available_tokens) + int(amount)
            conn.execute(
                "UPDATE practice_wallets SET available_tokens=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (balance, uid),
            )
            ledger_id = _insert_ledger(
                conn,
                user_id=uid,
                event_type="grant",
                amount=int(amount),
                balance_after=balance,
                reason="payment_succeeded",
                source=source,
                package_id=package_id,
                provider=provider,
                provider_payment_id=provider_payment_id,
                idempotency_key=idempotency_key,
            )
            wallet_after = _get_wallet_in_conn(conn, uid)
    return True, wallet_after, ledger_id


def _existing_reserved(
    conn: Any,
    *,
    user_id: int,
    session_id: int | None,
    audio_anchor: int | None,
) -> Any | None:
    if session_id is not None:
        return conn.execute(
            """
            SELECT reservation_id FROM practice_reservations
            WHERE user_id=? AND session_id=? AND status='reserved'
            ORDER BY created_at, reservation_id LIMIT 1
            """.strip(),
            (int(user_id), int(session_id)),
        ).fetchone()
    if audio_anchor is not None:
        return conn.execute(
            """
            SELECT reservation_id FROM practice_reservations
            WHERE user_id=? AND session_id IS NULL AND audio_anchor=? AND status='reserved'
            ORDER BY created_at, reservation_id LIMIT 1
            """.strip(),
            (int(user_id), int(audio_anchor)),
        ).fetchone()
    return None


def reserve_practice(
    user_id: int,
    *,
    session_id: int | None = None,
    audio_anchor: int | None = None,
    reason: str = "audio_delivery",
) -> tuple[bool, PracticeWallet, str | None]:
    """Reserve exactly one token with DB-level concurrency protection."""

    uid = _canonical_practice_user_id(int(user_id))
    reservation_id = f"practice_res_{uuid.uuid4().hex}"
    with db() as conn:
        with tx(conn):
            _ensure_wallet(conn, uid)
            existing = _existing_reserved(
                conn, user_id=uid, session_id=session_id, audio_anchor=audio_anchor
            )
            if existing:
                return True, _get_wallet_in_conn(conn, uid), str(existing["reservation_id"])

            insert_cursor = conn.execute(
                """
                INSERT OR IGNORE INTO practice_reservations(
                    reservation_id, user_id, amount, status, session_id, audio_anchor, reason
                ) VALUES(?,?,?,?,?,?,?)
                """.strip(),
                (
                    reservation_id, uid, 1, "reserved",
                    int(session_id) if session_id is not None else None,
                    int(audio_anchor) if audio_anchor is not None else None,
                    reason,
                ),
            )
            if int(getattr(insert_cursor, "rowcount", 0) or 0) <= 0:
                existing = _existing_reserved(
                    conn, user_id=uid, session_id=session_id, audio_anchor=audio_anchor
                )
                if existing:
                    return True, _get_wallet_in_conn(conn, uid), str(existing["reservation_id"])
                return False, _get_wallet_in_conn(conn, uid), None

            cursor = conn.execute(
                """
                UPDATE practice_wallets
                SET available_tokens=available_tokens - 1,
                    reserved_tokens=reserved_tokens + 1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND available_tokens > 0
                """.strip(),
                (uid,),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                conn.execute(
                    "DELETE FROM practice_reservations WHERE reservation_id=? AND status='reserved'",
                    (reservation_id,),
                )
                return False, _get_wallet_in_conn(conn, uid), None

            wallet_after = _get_wallet_in_conn(conn, uid)
            _insert_ledger(
                conn,
                user_id=uid,
                event_type="reserve",
                amount=-1,
                balance_after=int(wallet_after.available_tokens),
                reason=reason,
                idempotency_key=f"reserve:{reservation_id}",
            )
    return True, wallet_after, reservation_id


def _reservation_row(conn: Any, reservation_id: str) -> Any | None:
    return conn.execute(
        "SELECT * FROM practice_reservations WHERE reservation_id=?",
        (reservation_id,),
    ).fetchone()


def consume_reservation(reservation_id: str, *, reason: str = "audio_delivery_succeeded") -> bool:
    raw = str(reservation_id or "").strip()
    if not raw:
        return False
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            row = _reservation_row(conn, raw)
            if not row:
                return False
            status = str(row["status"])
            if status == "consumed":
                return True
            if status != "reserved":
                return False

            cursor = conn.execute(
                """
                UPDATE practice_reservations
                SET status='consumed', updated_at=CURRENT_TIMESTAMP
                WHERE reservation_id=? AND status='reserved'
                """.strip(),
                (raw,),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                current = _reservation_row(conn, raw)
                return bool(current and str(current["status"]) == "consumed")

            user_id = int(row["user_id"])
            amount = int(row["amount"])
            conn.execute(
                """
                UPDATE practice_wallets
                SET reserved_tokens=CASE WHEN reserved_tokens >= ? THEN reserved_tokens - ? ELSE 0 END,
                    used_tokens=used_tokens + ?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=?
                """.strip(),
                (amount, amount, amount, user_id),
            )
            wallet_after = _get_wallet_in_conn(conn, user_id)
            _insert_ledger(
                conn,
                user_id=user_id,
                event_type="consume",
                amount=-amount,
                balance_after=int(wallet_after.available_tokens),
                reason=reason,
                idempotency_key=f"consume:{raw}",
            )
    return True


def release_reservation(reservation_id: str, *, reason: str = "audio_delivery_failed") -> bool:
    raw = str(reservation_id or "").strip()
    if not raw:
        return False
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            row = _reservation_row(conn, raw)
            if not row:
                return False
            status = str(row["status"])
            if status == "released":
                return True
            if status != "reserved":
                return False

            cursor = conn.execute(
                """
                UPDATE practice_reservations
                SET status='released', updated_at=CURRENT_TIMESTAMP
                WHERE reservation_id=? AND status='reserved'
                """.strip(),
                (raw,),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
                current = _reservation_row(conn, raw)
                return bool(current and str(current["status"]) == "released")

            user_id = int(row["user_id"])
            amount = int(row["amount"])
            conn.execute(
                """
                UPDATE practice_wallets
                SET available_tokens=available_tokens + ?,
                    reserved_tokens=CASE WHEN reserved_tokens >= ? THEN reserved_tokens - ? ELSE 0 END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE user_id=?
                """.strip(),
                (amount, amount, amount, user_id),
            )
            wallet_after = _get_wallet_in_conn(conn, user_id)
            _insert_ledger(
                conn,
                user_id=user_id,
                event_type="release",
                amount=amount,
                balance_after=int(wallet_after.available_tokens),
                reason=reason,
                idempotency_key=f"release:{raw}",
            )
    return True


def check_and_reserve_for_audio(
    user_id: int,
    *,
    is_demo: bool,
    session_id: int | None = None,
    audio_anchor: int | None = None,
) -> PracticeAccessDecision:
    mode = enforcement_mode()
    if is_demo or not token_economy_enabled() or mode == "off":
        return PracticeAccessDecision(True, mode, "free_demo_or_disabled")

    uid = _canonical_practice_user_id(int(user_id))
    with db() as conn:
        _ensure_wallet(conn, uid)
        existing = _existing_reserved(
            conn, user_id=uid, session_id=session_id, audio_anchor=audio_anchor
        )
        if existing:
            return PracticeAccessDecision(
                True, mode, "existing_reservation", reservation_id=str(existing["reservation_id"])
            )
        wallet = _get_wallet_in_conn(conn, uid)

    if wallet.available_tokens <= 0:
        if mode == "soft":
            return PracticeAccessDecision(
                True, mode, "soft_insufficient_balance", warning=EMPTY_BALANCE_MESSAGE
            )
        return PracticeAccessDecision(
            False, mode, "insufficient_balance", message=EMPTY_BALANCE_MESSAGE
        )

    ok, _wallet_after, reservation_id = reserve_practice(
        uid, session_id=session_id, audio_anchor=audio_anchor
    )
    if not ok:
        if mode == "soft":
            return PracticeAccessDecision(True, mode, "soft_reserve_failed", warning=RESERVE_FAILED_MESSAGE)
        return PracticeAccessDecision(False, mode, "reserve_failed", message=RESERVE_FAILED_MESSAGE)
    return PracticeAccessDecision(True, mode, "reserved", reservation_id=reservation_id)


def finalize_audio_access(decision: PracticeAccessDecision, *, delivered: bool) -> bool:
    """Finalize an audio reservation idempotently."""

    if not decision.reservation_id:
        return True
    if delivered:
        return consume_reservation(decision.reservation_id)
    return release_reservation(decision.reservation_id)


def grant_tokens_for_payment(
    *,
    provider: str,
    provider_payment_id: str,
    user_id: int,
    package_id: str,
    source: str = "webhook",
) -> tuple[bool, PracticeWallet, int | None]:
    uid = _canonical_practice_user_id(int(user_id))
    package = get_package(package_id)
    with db() as conn:
        with tx(conn):
            _ensure_wallet(conn, uid)
            existing = conn.execute(
                "SELECT ledger_id FROM payment_token_grants WHERE provider=? AND provider_payment_id=?",
                (provider, provider_payment_id),
            ).fetchone()
            if existing:
                return False, _get_wallet_in_conn(conn, uid), int(existing["ledger_id"] or 0)

    inserted, wallet, ledger_id = grant_tokens(
        uid,
        package_id=package.package_id,
        amount=package.tokens,
        provider=provider,
        provider_payment_id=provider_payment_id,
        source=source,
        idempotency_key=f"payment_grant:{provider}:{provider_payment_id}",
    )
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO payment_token_grants(
                    provider, provider_payment_id, user_id, package_id, tokens_granted, ledger_id
                ) VALUES(?,?,?,?,?,?)
                """.strip(),
                (provider, provider_payment_id, uid, package.package_id, package.tokens, ledger_id),
            )
    return inserted, wallet, ledger_id


def get_delivery_mode(user_id: int) -> str:
    uid = _canonical_practice_user_id(int(user_id))
    with db() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT delivery_mode FROM user_practice_preferences WHERE user_id=?",
            (uid,),
        ).fetchone()
    return str(row["delivery_mode"] if row else "single_daily")


def set_delivery_mode(user_id: int, mode: str) -> str:
    uid = _canonical_practice_user_id(int(user_id))
    mode = normalize_delivery_mode(mode)
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO user_practice_preferences(user_id, delivery_mode, updated_at)
                VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    delivery_mode=excluded.delivery_mode,
                    updated_at=CURRENT_TIMESTAMP
                """.strip(),
                (uid, mode),
            )
    return mode


def payment_url(
    base_url: str,
    *,
    user_id: int,
    platform: str,
    external_user_id: str | None,
    package_id: str,
    gift_token: str | None = None,
) -> str:
    public_id = (external_user_id or "").strip() or str(int(user_id))
    params = {
        "source": platform or "messenger",
        "user_id": public_id,
        "kind": "tokens",
        "package_id": package_id,
    }
    if str(gift_token or "").strip():
        params["gift_token"] = str(gift_token).strip()
        params["gift"] = "1"
    return f"{base_url.rstrip('/')}/pay/yookassa?{urllib.parse.urlencode(params)}"


def _price_label(price_rub: int) -> str:
    return f"{int(price_rub):,} ₽".replace(",", " ")


def _mode_title(mode: str) -> str:
    normalized = normalize_delivery_mode(mode)
    if normalized == "morning_only":
        return "только утро"
    if normalized == "evening_only":
        return "только вечер"
    if normalized == "both":
        return "утро + вечер"
    if normalized == "paused":
        return "пауза"
    return "1 практика в день"


def render_packages_text(
    user_id: int,
    *,
    base_url: str,
    platform: str,
    external_user_id: str | None = None,
) -> str:
    wallet = get_wallet(int(user_id))
    mode = get_delivery_mode(int(user_id))
    cost = daily_practice_cost(mode)
    days = "пауза" if cost <= 0 else f"примерно на {wallet.available_tokens // cost} дн. при текущем ритме"
    lines = [
        "💳 Пакеты практик", "",
        "1 практика = одно аудио с оценкой состояния ДО и ПОСЛЕ.",
        "Если аудио не отправилось, практика не списывается.", "",
        f"Сейчас у Вас: {wallet.available_tokens} практик.",
        f"Ритм: {_mode_title(mode)} ({days}).", "", "Выберите пакет:",
    ]
    for package in get_active_packages():
        lines.append("")
        lines.append(f"{package.title} — {_price_label(package.price_rub)}")
        lines.append(package.description)
        lines.append(
            payment_url(
                base_url,
                user_id=int(user_id),
                platform=platform,
                external_user_id=external_user_id,
                package_id=package.package_id,
            )
        )
    lines.extend([
        "",
        "После оплаты практики будут начислены на баланс.",
        "Ритм можно менять: только утро, только вечер, утро + вечер или пауза.",
    ])
    return "\n".join(lines).strip()


def render_rhythm_text(user_id: int) -> str:
    mode = get_delivery_mode(int(user_id))
    wallet = get_wallet(int(user_id))
    cost = daily_practice_cost(mode)
    days = (
        "практики не расходуются, пока стоит пауза"
        if cost <= 0
        else f"баланса хватит примерно на {wallet.available_tokens // cost} дн."
    )
    return (
        "🕒 Ритм практик\n\n"
        f"Сейчас: {_mode_title(mode)}.\n"
        f"Баланс: {wallet.available_tokens} практик; {days}.\n\n"
        "Можно выбрать:\n"
        "🌅 Только утро — 1 практика в день\n"
        "🌙 Только вечер — 1 практика в день\n"
        "🌅🌙 Утро + вечер — 2 практики в день\n"
        "⏸ Пауза — ничего не отправлять"
    )
