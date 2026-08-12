from __future__ import annotations

from typing import Any

from services.db import db
from services.practice_token_contract import daily_practice_cost, normalize_delivery_mode


def _rowdict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _refill_threshold(delivery_mode: str | None) -> int:
    mode = normalize_delivery_mode(delivery_mode)
    return max(2, daily_practice_cost(mode) * 2)


def _display_name(row: dict[str, Any]) -> str:
    username = str(row.get("username") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    first_name = str(row.get("first_name") or "").strip()
    if first_name:
        return first_name
    return f"ID {int(row['user_id'])}"


def retention_money_snapshot(*, limit: int = 10) -> dict[str, Any]:
    """Build an admin-only retention read model from canonical money/token state.

    ``payment_token_grants`` is the successful paid-grant authority. Wallet and
    delivery preference tables are joined only to decide whether an already
    paying user is approaching the same delivery-aware refill boundary used by
    the access contract. No payment, access, wallet or messaging state is
    mutated here.
    """

    safe_limit = max(1, min(int(limit), 20))
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                g.user_id,
                COUNT(*) AS purchase_count,
                MAX(g.created_at) AS last_purchase_at,
                COALESCE(w.available_tokens, 0) AS available_tokens,
                COALESCE(w.reserved_tokens, 0) AS reserved_tokens,
                COALESCE(w.used_tokens, 0) AS used_tokens,
                COALESCE(pref.delivery_mode, 'single_daily') AS delivery_mode,
                u.username,
                u.first_name
            FROM payment_token_grants AS g
            LEFT JOIN practice_wallets AS w ON w.user_id = g.user_id
            LEFT JOIN user_practice_preferences AS pref ON pref.user_id = g.user_id
            LEFT JOIN users AS u ON u.user_id = g.user_id
            GROUP BY
                g.user_id,
                w.available_tokens,
                w.reserved_tokens,
                w.used_tokens,
                pref.delivery_mode,
                u.username,
                u.first_name
            ORDER BY MAX(g.created_at) DESC, g.user_id ASC
            """.strip()
        ).fetchall()

    buyers = [_rowdict(row) for row in rows]
    paying_users = len(buyers)
    repeat_buyers = 0
    positive_balance_users = 0
    low_balance_users = 0
    zero_balance_users = 0
    candidates: list[dict[str, Any]] = []

    for row in buyers:
        purchase_count = int(row.get("purchase_count") or 0)
        available = int(row.get("available_tokens") or 0)
        reserved = int(row.get("reserved_tokens") or 0)
        used = int(row.get("used_tokens") or 0)
        mode = normalize_delivery_mode(str(row.get("delivery_mode") or ""))
        threshold = _refill_threshold(mode)

        if purchase_count >= 2:
            repeat_buyers += 1
        if available > 0:
            positive_balance_users += 1

        segment = ""
        action = ""
        priority = 99
        if 0 < available <= threshold:
            low_balance_users += 1
            segment = "низкий баланс"
            action = "предложить пополнение до остановки маршрута"
            priority = 0
        elif available <= 0:
            zero_balance_users += 1
            segment = "баланс 0"
            action = "вернуть мягким предложением продолжить маршрут"
            priority = 1

        if segment:
            candidates.append(
                {
                    "user_id": int(row["user_id"]),
                    "name": _display_name(row),
                    "segment": segment,
                    "action": action,
                    "available_tokens": available,
                    "reserved_tokens": reserved,
                    "used_tokens": used,
                    "purchase_count": purchase_count,
                    "delivery_mode": mode,
                    "refill_threshold": threshold,
                    "last_purchase_at": row.get("last_purchase_at"),
                    "priority": priority,
                }
            )

    candidates.sort(key=lambda item: int(item["priority"]))
    repeat_rate = (repeat_buyers / paying_users * 100.0) if paying_users else 0.0
    return {
        "paying_users": paying_users,
        "repeat_buyers": repeat_buyers,
        "repeat_purchase_rate": repeat_rate,
        "positive_balance_users": positive_balance_users,
        "healthy_balance_users": max(positive_balance_users - low_balance_users, 0),
        "low_balance_users": low_balance_users,
        "zero_balance_users": zero_balance_users,
        "refill_candidates": candidates[:safe_limit],
    }


def render_retention_money(snapshot: dict[str, Any]) -> str:
    lines = [
        "💰 Retention / повторные деньги",
        "",
        f"Плативших за пакеты: {int(snapshot.get('paying_users') or 0)}",
        (
            "Повторно покупали: "
            f"{int(snapshot.get('repeat_buyers') or 0)} "
            f"({float(snapshot.get('repeat_purchase_rate') or 0.0):.1f}%)"
        ),
        f"С положительным балансом: {int(snapshot.get('positive_balance_users') or 0)}",
        f"Низкий баланс: {int(snapshot.get('low_balance_users') or 0)}",
        f"Баланс 0 после покупки: {int(snapshot.get('zero_balance_users') or 0)}",
    ]

    candidates = list(snapshot.get("refill_candidates") or [])
    if candidates:
        lines.extend(["", "🎯 Кому действовать сейчас:"])
        for item in candidates:
            balance = int(item.get("available_tokens") or 0)
            purchases = int(item.get("purchase_count") or 0)
            lines.append(
                f"• {item.get('name')}: {item.get('segment')}, "
                f"остаток {balance}, покупок {purchases} — {item.get('action')}"
            )
    else:
        lines.extend(["", "🎯 Сейчас нет плативших пользователей на refill/win-back границе."])

    lines.extend(
        [
            "",
            "Низкий баланс считается по тому же двухдневному delivery-aware порогу, "
            "что и пользовательский refill-сигнал.",
        ]
    )
    return "\n".join(lines)
