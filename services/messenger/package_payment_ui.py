from __future__ import annotations

import re
import sqlite3
import urllib.parse
from dataclasses import dataclass
from typing import Any

from services.db import db
from services.gift_claims import create_gift_checkout_token
from services.messenger.platforms import normalize_platform
from services.payments.checkout_intent import add_checkout_intent_to_url
from services.payments.public_url import payment_public_base_url
from services.practice_token_contract import PracticePackage, public_practice_packages


@dataclass(frozen=True)
class PackagePaymentLink:
    package_id: str
    title: str
    description: str
    price_rub: int
    url: str
    gift_token: str = ""

    @property
    def label(self) -> str:
        return f"{self.title} — {_price_label(self.price_rub)}"


@dataclass(frozen=True)
class PaymentIdentity:
    user_id: int
    platform: str
    external_user_id: str | None


def _row_get(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        try:
            return row[index]
        except (TypeError, IndexError):
            return None


def resolve_payment_identity(*, user_id: int, platform: str, external_user_id: str | None = None) -> PaymentIdentity:
    fallback_user_id = int(user_id)
    norm_platform = normalize_platform(platform)
    ext = str(external_user_id or "").strip() or None
    canonical_user_id = fallback_user_id

    if ext:
        try:
            with db() as conn:
                row = conn.execute(
                    """
                    SELECT user_id
                    FROM user_channel_identities
                    WHERE platform=? AND external_user_id=?
                    ORDER BY last_seen_at DESC
                    LIMIT 1
                    """.strip(),
                    (norm_platform, ext),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            if "user_channel_identities" not in str(exc):
                raise
            row = None

        value = _row_get(row, "user_id", 0)
        if value is not None:
            try:
                resolved = int(str(value).strip())
            except (TypeError, ValueError):
                resolved = 0
            if resolved > 0:
                canonical_user_id = resolved

    return PaymentIdentity(user_id=canonical_user_id, platform=norm_platform, external_user_id=ext)


def _price_label(price_rub: int) -> str:
    return f"{int(price_rub):,} ₽".replace(",", " ")


def _canonical_payment_url(
    base_url: str,
    *,
    user_id: int,
    platform: str,
    external_user_id: str | None,
    package_id: str,
    gift_token: str = "",
) -> str:
    """Build public checkout URL with canonical identity first.

    VK/MAX may have transport-specific identifiers. Public checkout, signed
    intent and YooKassa reconciliation must use the canonical profile user_id;
    the messenger id is diagnostic metadata only.
    """
    canonical_user_id = str(int(user_id))
    messenger_external_user_id = str(external_user_id or "").strip()
    params = {
        "source": platform or "messenger",
        "user_id": canonical_user_id,
        "kind": "tokens",
        "package_id": package_id,
    }
    if messenger_external_user_id and messenger_external_user_id != canonical_user_id:
        params["external_user_id"] = messenger_external_user_id
    if str(gift_token or "").strip():
        params["gift_token"] = str(gift_token).strip()
        params["gift"] = "1"
    return f"{base_url.rstrip('/')}/pay/yookassa?{urllib.parse.urlencode(params)}"


def package_payment_links(
    *,
    user_id: int,
    platform: str,
    external_user_id: str | None = None,
    as_gift: bool = False,
) -> tuple[PackagePaymentLink, ...]:
    identity = resolve_payment_identity(user_id=int(user_id), platform=platform, external_user_id=external_user_id)
    base_url = payment_public_base_url()
    items: list[PackagePaymentLink] = []
    for package in public_practice_packages():
        gift_token = (
            create_gift_checkout_token(
                buyer_user_id=identity.user_id,
                package_id=package.package_id,
                source_platform=identity.platform,
            )
            if as_gift
            else ""
        )
        items.append(
            _package_link(
                package,
                base_url=base_url,
                user_id=identity.user_id,
                platform=identity.platform,
                external_user_id=identity.external_user_id,
                gift_token=gift_token,
            )
        )
    return tuple(items)


def _package_link(
    package: PracticePackage,
    *,
    base_url: str,
    user_id: int,
    platform: str,
    external_user_id: str | None,
    gift_token: str = "",
) -> PackagePaymentLink:
    raw_url = _canonical_payment_url(
        base_url,
        user_id=int(user_id),
        platform=platform,
        external_user_id=external_user_id,
        package_id=package.package_id,
        gift_token=gift_token,
    )
    return PackagePaymentLink(
        package_id=package.package_id,
        title=package.title,
        description=package.description,
        price_rub=package.price_rub,
        gift_token=gift_token,
        url=add_checkout_intent_to_url(
            raw_url,
            user_id=str(int(user_id)),
            package_id=package.package_id,
            kind="tokens",
            source=platform,
            gift_token=gift_token or None,
        ),
    )


def package_payment_text(*, user_id: int, platform: str, external_user_id: str | None = None) -> str:
    lines = [
        "💳 Тарифы Метротерапии",
        "",
        "Выберите пакет практик. Это та же витрина, что в Telegram: 4 актуальных пакета, без старых morning/evening/both тарифов.",
        "",
    ]
    for item in package_payment_links(user_id=int(user_id), platform=platform, external_user_id=external_user_id):
        lines.extend([
            item.label,
            item.description,
            item.url,
            "",
        ])
    lines.extend([
        "После оплаты практики будут начислены на баланс. Если пакет включает видеокурс или консультацию, доставка пойдёт через общий premium delivery/outbox.",
        "После оплаты вернитесь сюда и нажмите «🎧 Получить аудио». Если кнопки нет, отправьте: continue",
    ])
    return "\n".join(lines).strip()


def gift_package_text(*, user_id: int, platform: str, external_user_id: str | None = None) -> str:
    lines = [
        "🎁 Подарить Метротерапию",
        "",
        "Выберите пакет ниже. После успешной оплаты будет активна подарочная claim-ссылка вида claim gift_... — отправьте её человеку, которому дарите практики.",
        "",
    ]
    for item in package_payment_links(user_id=int(user_id), platform=platform, external_user_id=external_user_id, as_gift=True):
        claim_text = f"claim {item.gift_token}" if item.gift_token else "claim-ссылка будет создана после оплаты"
        lines.extend([
            item.label,
            item.description,
            item.url,
            f"После оплаты отправьте получателю: {claim_text}",
            "",
        ])
    lines.extend([
        "Получатель может отправить эту claim-команду в Telegram/VK/MAX. После активации пакет закрепится за его профилем.",
        "Если нужен перенос прогресса между Telegram/VK/MAX — отправьте: switch",
    ])
    return "\n".join(lines).strip()


_PRICE_LABEL_RE = re.compile(r"\d[\d\s]*\s*₽")


def _looks_like_package_label(value: str) -> bool:
    raw = str(value or "").strip()
    return "—" in raw and "₽" in raw and bool(_PRICE_LABEL_RE.search(raw))


def extract_labeled_urls(text: str) -> tuple[tuple[str, str], ...]:
    lines = [line.strip() for line in str(text or "").splitlines()]
    pairs: list[tuple[str, str]] = []
    for idx, line in enumerate(lines):
        if not re.match(r"^https?://", line):
            continue
        label = "Открыть"
        for previous in reversed(lines[:idx]):
            if not previous or previous.startswith("http"):
                continue
            if _looks_like_package_label(previous):
                label = previous
                break
        pairs.append((label, line.rstrip(".,;")))
    return tuple(pairs)
