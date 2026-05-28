from __future__ import annotations

import re
from dataclasses import dataclass

from services.payments.public_url import payment_public_base_url
from services.practice_token_contract import PracticePackage, public_practice_packages
from services.practice_tokens import payment_url


@dataclass(frozen=True)
class PackagePaymentLink:
    package_id: str
    title: str
    description: str
    price_rub: int
    url: str

    @property
    def label(self) -> str:
        return f"{self.title} — {_price_label(self.price_rub)}"


def _price_label(price_rub: int) -> str:
    return f"{int(price_rub):,} ₽".replace(",", " ")


def package_payment_links(*, user_id: int, platform: str, external_user_id: str | None = None) -> tuple[PackagePaymentLink, ...]:
    base_url = payment_public_base_url()
    items: list[PackagePaymentLink] = []
    for package in public_practice_packages():
        items.append(_package_link(package, base_url=base_url, user_id=user_id, platform=platform, external_user_id=external_user_id))
    return tuple(items)


def _package_link(
    package: PracticePackage,
    *,
    base_url: str,
    user_id: int,
    platform: str,
    external_user_id: str | None,
) -> PackagePaymentLink:
    return PackagePaymentLink(
        package_id=package.package_id,
        title=package.title,
        description=package.description,
        price_rub=package.price_rub,
        url=payment_url(
            base_url,
            user_id=int(user_id),
            platform=platform,
            external_user_id=external_user_id,
            package_id=package.package_id,
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
        "Подарочная витрина использует те же 4 актуальных пакета, что и Telegram. После оплаты можно отправить человеку ссылку на проект или switch-ссылку; полноценный claim-flow будет отдельным gift-контуром.",
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
        "Ссылка на проект для отправки человеку: https://metrotherapy.ru",
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
