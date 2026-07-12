from __future__ import annotations

import urllib.parse

from services.practice_token_contract import daily_practice_cost, normalize_delivery_mode
from services.practice_tokens_access import (
    _delivered_reservation_ids,
    _existing_reserved,
    _reservation_row,
    check_and_reserve_for_audio,
    consume_reservation,
    finalize_audio_access,
    get_wallet,
    has_paid_practice_access,
    reconcile_delivered_reservations,
    release_reservation,
    reserve_practice,
)
from services.practice_tokens_wallet import (
    EMPTY_BALANCE_MESSAGE,
    RESERVE_FAILED_MESSAGE,
    PracticeAccessDecision,
    PracticeWallet,
    canonical_practice_user_id,
    enforcement_mode,
    ensure_schema,
    ensure_wallet,
    get_active_packages,
    get_delivery_mode,
    get_package,
    get_wallet_in_conn,
    grant_tokens,
    grant_tokens_for_payment,
    insert_ledger,
    set_delivery_mode,
    token_access_authoritative,
    token_economy_enabled,
    wallet_from_row,
)

# Backward-compatible private aliases for older focused tests and migration helpers.
_canonical_practice_user_id = canonical_practice_user_id
_wallet_from_row = wallet_from_row
_ensure_wallet = ensure_wallet
_get_wallet_in_conn = get_wallet_in_conn
_insert_ledger = insert_ledger


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
    days = (
        "пауза"
        if cost <= 0
        else f"примерно на {wallet.available_tokens // cost} дн. при текущем ритме"
    )
    lines = [
        "💳 Пакеты практик",
        "",
        "1 практика = одно аудио с оценкой состояния ДО и ПОСЛЕ.",
        "Если аудио не отправилось, практика не списывается.",
        "",
        f"Сейчас у Вас: {wallet.available_tokens} практик.",
        f"Ритм: {_mode_title(mode)} ({days}).",
        "",
        "Выберите пакет:",
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


__all__ = [
    "EMPTY_BALANCE_MESSAGE",
    "RESERVE_FAILED_MESSAGE",
    "PracticeAccessDecision",
    "PracticeWallet",
    "canonical_practice_user_id",
    "check_and_reserve_for_audio",
    "consume_reservation",
    "daily_practice_cost",
    "enforcement_mode",
    "ensure_schema",
    "finalize_audio_access",
    "get_active_packages",
    "get_delivery_mode",
    "get_package",
    "get_wallet",
    "grant_tokens",
    "grant_tokens_for_payment",
    "has_paid_practice_access",
    "payment_url",
    "reconcile_delivered_reservations",
    "release_reservation",
    "render_packages_text",
    "render_rhythm_text",
    "reserve_practice",
    "set_delivery_mode",
    "token_access_authoritative",
    "token_economy_enabled",
]
