from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Any

from services.db import db, tx
from services.practice_token_contract import DEFAULT_PRACTICE_PACKAGES, PracticePackage, daily_practice_cost, normalize_delivery_mode, package_by_id


@dataclass(frozen=True)
class PracticeWallet:
    user_id: int
    available_tokens: int = 0
    reserved_tokens: int = 0
    used_tokens: int = 0


def token_economy_enabled() -> bool:
    raw = (os.getenv("TOKEN_ECONOMY_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def enforcement_mode() -> str:
    raw = (os.getenv("TOKEN_ENFORCEMENT_MODE") or "off").strip().lower()
    if raw in {"hard", "1", "true", "on"}:
        return "hard"
    if raw in {"soft", "warn", "warning"}:
        return "soft"
    return "off"


def ensure_schema(conn: Any) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS practice_wallets(user_id INTEGER PRIMARY KEY, available_tokens INTEGER NOT NULL DEFAULT 0, reserved_tokens INTEGER NOT NULL DEFAULT 0, used_tokens INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS practice_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, event_type TEXT NOT NULL, amount INTEGER NOT NULL, balance_after INTEGER NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', package_id TEXT, provider TEXT, provider_payment_id TEXT, idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS payment_token_grants(provider TEXT NOT NULL, provider_payment_id TEXT NOT NULL, user_id INTEGER NOT NULL, package_id TEXT NOT NULL, tokens_granted INTEGER NOT NULL, ledger_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(provider, provider_payment_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS user_practice_preferences(user_id INTEGER PRIMARY KEY, delivery_mode TEXT NOT NULL DEFAULT 'single_daily', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")


def get_active_packages() -> tuple[PracticePackage, ...]:
    return DEFAULT_PRACTICE_PACKAGES


def get_package(package_id: str | None) -> PracticePackage:
    return package_by_id(package_id)


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
    conn.execute("INSERT OR IGNORE INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)", (int(user_id), 0, 0, 0))


def _get_wallet_in_conn(conn: Any, user_id: int) -> PracticeWallet:
    row = conn.execute(
        "SELECT user_id, available_tokens, reserved_tokens, used_tokens FROM practice_wallets WHERE user_id=?",
        (int(user_id),),
    ).fetchone()
    return _wallet_from_row(row, int(user_id))


def get_wallet(user_id: int) -> PracticeWallet:
    try:
        with db() as conn:
            _ensure_wallet(conn, int(user_id))
            return _get_wallet_in_conn(conn, int(user_id))
    except Exception:  # validator: allow-wide-except
        return PracticeWallet(int(user_id), 0, 0, 0)


def grant_tokens(user_id: int, *, package_id: str, amount: int, provider: str = "manual", provider_payment_id: str = "", source: str = "", idempotency_key: str | None = None) -> tuple[bool, PracticeWallet, int | None]:
    idempotency_key = idempotency_key or f"grant:{provider}:{provider_payment_id}:{user_id}:{package_id}:{amount}"
    with db() as conn:
        with tx(conn):
            _ensure_wallet(conn, int(user_id))
            existing = conn.execute("SELECT id FROM practice_ledger WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                return False, _get_wallet_in_conn(conn, int(user_id)), int(existing["id"])
            wallet = _get_wallet_in_conn(conn, int(user_id))
            balance = int(wallet.available_tokens) + int(amount)
            conn.execute("UPDATE practice_wallets SET available_tokens=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (balance, int(user_id)))
            conn.execute("INSERT INTO practice_ledger(user_id, event_type, amount, balance_after, reason, source, package_id, provider, provider_payment_id, idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)", (int(user_id), "grant", int(amount), balance, "payment_succeeded", source, package_id, provider, provider_payment_id, idempotency_key))
            row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
            ledger_id = int(row["id"] if row else 0)
            wallet_after = _get_wallet_in_conn(conn, int(user_id))
    return True, wallet_after, ledger_id


def grant_tokens_for_payment(*, provider: str, provider_payment_id: str, user_id: int, package_id: str, source: str = "webhook") -> tuple[bool, PracticeWallet, int | None]:
    package = get_package(package_id)
    with db() as conn:
        with tx(conn):
            _ensure_wallet(conn, int(user_id))
            existing = conn.execute("SELECT ledger_id FROM payment_token_grants WHERE provider=? AND provider_payment_id=?", (provider, provider_payment_id)).fetchone()
            if existing:
                return False, _get_wallet_in_conn(conn, int(user_id)), int(existing["ledger_id"] or 0)
    inserted, wallet, ledger_id = grant_tokens(int(user_id), package_id=package.package_id, amount=package.tokens, provider=provider, provider_payment_id=provider_payment_id, source=source, idempotency_key=f"payment_grant:{provider}:{provider_payment_id}")
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute("INSERT OR IGNORE INTO payment_token_grants(provider, provider_payment_id, user_id, package_id, tokens_granted, ledger_id) VALUES(?,?,?,?,?,?)", (provider, provider_payment_id, int(user_id), package.package_id, package.tokens, ledger_id))
    return inserted, wallet, ledger_id


def get_delivery_mode(user_id: int) -> str:
    try:
        with db() as conn:
            ensure_schema(conn)
            row = conn.execute("SELECT delivery_mode FROM user_practice_preferences WHERE user_id=?", (int(user_id),)).fetchone()
    except Exception:  # validator: allow-wide-except
        return "single_daily"
    return str(row["delivery_mode"] if row else "single_daily")


def set_delivery_mode(user_id: int, mode: str) -> str:
    mode = normalize_delivery_mode(mode)
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute("INSERT INTO user_practice_preferences(user_id, delivery_mode, updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET delivery_mode=excluded.delivery_mode, updated_at=CURRENT_TIMESTAMP", (int(user_id), mode))
    return mode


def payment_url(base_url: str, *, user_id: int, platform: str, external_user_id: str | None, package_id: str) -> str:
    public_id = (external_user_id or "").strip() or str(int(user_id))
    params = urllib.parse.urlencode({"source": platform or "messenger", "user_id": public_id, "kind": "tokens", "package_id": package_id})
    return f"{base_url.rstrip('/')}/pay/yookassa?{params}"


def render_packages_text(user_id: int, *, base_url: str, platform: str, external_user_id: str | None = None) -> str:
    wallet = get_wallet(int(user_id))
    lines = ["💳 Пакеты практик", "", f"Ваш баланс: {wallet.available_tokens} практик.", "", "1 практика = одно аудио с оценкой состояния ДО и ПОСЛЕ.", "Если аудио не отправилось, практика не списывается.", ""]
    for package in get_active_packages():
        lines.append(f"{package.title} — {package.price_rub:,} ₽".replace(",", " "))
        lines.append(package.description)
        lines.append(payment_url(base_url, user_id=int(user_id), platform=platform, external_user_id=external_user_id, package_id=package.package_id))
        lines.append("")
    lines.append("Ритм можно выбрать отдельно: только утро, только вечер или утро + вечер.")
    lines.append("В режиме “утро + вечер” расходуется 2 практики в день.")
    return "\n".join(lines).strip()


def render_rhythm_text(user_id: int) -> str:
    mode = get_delivery_mode(int(user_id))
    wallet = get_wallet(int(user_id))
    cost = daily_practice_cost(mode)
    title = {"single_daily": "одна практика в день", "morning_only": "только утро", "evening_only": "только вечер", "both": "утро + вечер", "paused": "пауза"}.get(mode, mode)
    days = "—" if cost <= 0 else str(wallet.available_tokens // cost)
    return f"🕒 Ваш ритм\n\nСейчас выбран режим: {title}.\nБаланс: {wallet.available_tokens} практик.\nОриентировочно хватит на дней: {days}.\n\nКоманды: rhythm morning, rhythm evening, rhythm both, rhythm pause."
