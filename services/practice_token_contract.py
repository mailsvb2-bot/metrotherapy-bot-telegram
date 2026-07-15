from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


@dataclass(frozen=True)
class PracticePackage:
    package_id: str
    title: str
    description: str
    tokens: int
    price_rub: int
    sort_order: int = 100
    public: bool = True
    badge: str = ""
    price_xtr: int = 0


DEFAULT_PRACTICE_PACKAGES: tuple[PracticePackage, ...] = (
    PracticePackage(
        "practice_start_7",
        "Стартовый пакет",
        "7 практик. Мягкий вход и проверка формата.",
        7,
        1900,
        10,
        True,
        "",
        1226,
    ),
    PracticePackage(
        "practice_60",
        "Полный маршрут",
        "60 практик. Базовый месячный маршрут.",
        60,
        7900,
        20,
        True,
        "",
        5099,
    ),
    PracticePackage(
        "practice_antistress_60",
        "Антистресс-система",
        "60 практик + доступ к видеокурсу.",
        60,
        12900,
        30,
        True,
        "",
        8327,
    ),
    PracticePackage(
        "practice_personal_month",
        "Персональный месяц",
        "60 практик + видеокурс + заявка на консультацию.",
        60,
        23000,
        40,
        True,
        "",
        14847,
    ),
)

LEGACY_PRACTICE_PACKAGES: tuple[PracticePackage, ...] = (
    PracticePackage("practice_5", "5 practices", "Legacy trial package", 5, 990, 900, False, "", 0),
    PracticePackage("practice_20", "20 practices", "Legacy base route", 20, 3490, 910, False, "", 0),
)

ALL_PRACTICE_PACKAGES: tuple[PracticePackage, ...] = DEFAULT_PRACTICE_PACKAGES + LEGACY_PRACTICE_PACKAGES

VALID_DELIVERY_MODES = frozenset(
    {
        "single_daily",
        "morning_only",
        "evening_only",
        "both",
        "paused",
    }
)


MORNING_RU = "\u0443\u0442\u0440\u043e"
MORNING_ONLY_RU = "\u0442\u043e\u043b\u044c\u043a\u043e \u0443\u0442\u0440\u043e"
EVENING_RU = "\u0432\u0435\u0447\u0435\u0440"
EVENING_ONLY_RU = "\u0442\u043e\u043b\u044c\u043a\u043e \u0432\u0435\u0447\u0435\u0440"
BOTH_RU = "\u0443\u0442\u0440\u043e + \u0432\u0435\u0447\u0435\u0440"
PAUSE_RU = "\u043f\u0430\u0443\u0437\u0430"


def normalize_delivery_mode(raw: str | None) -> str:
    value = (raw or "").strip().casefold().replace("\u0451", "\u0435")
    aliases = {
        "": "single_daily",
        "1": "single_daily",
        "single": "single_daily",
        "single_daily": "single_daily",
        "one": "single_daily",
        "once": "single_daily",
        "morning": "morning_only",
        "morning_only": "morning_only",
        MORNING_RU: "morning_only",
        MORNING_ONLY_RU: "morning_only",
        "evening": "evening_only",
        "evening_only": "evening_only",
        EVENING_RU: "evening_only",
        EVENING_ONLY_RU: "evening_only",
        "both": "both",
        MORNING_RU + " " + EVENING_RU: "both",
        MORNING_RU + "+" + EVENING_RU: "both",
        BOTH_RU: "both",
        "pause": "paused",
        "paused": "paused",
        PAUSE_RU: "paused",
    }
    return aliases.get(value, "single_daily")


def daily_practice_cost(mode: str) -> int:
    normalized = normalize_delivery_mode(mode)
    if normalized == "both":
        return 2
    if normalized == "paused":
        return 0
    return 1


def public_practice_packages() -> tuple[PracticePackage, ...]:
    return tuple(package for package in DEFAULT_PRACTICE_PACKAGES if package.public)


def package_by_id(package_id: str | None) -> PracticePackage:
    wanted = (package_id or "").strip()
    if not wanted:
        raise ValueError("Practice package id is required")
    for package in ALL_PRACTICE_PACKAGES:
        if package.package_id == wanted:
            return package
    raise ValueError(f"Unknown practice package: {wanted}")


def telegram_stars_enabled() -> bool:
    raw = (os.getenv("TELEGRAM_STARS_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _telegram_stars_env_key(package_id: str) -> str:
    suffix = "".join(ch if ch.isalnum() else "_" for ch in package_id.upper())
    return f"TELEGRAM_STARS_PRICE_{suffix}"


def telegram_stars_pricing_mode() -> str:
    mode = (os.getenv("TELEGRAM_STARS_PRICING_MODE") or "buyer_parity").strip().lower()
    if mode not in {"buyer_parity", "explicit"}:
        raise ValueError("Invalid TELEGRAM_STARS_PRICING_MODE")
    return mode


def telegram_stars_buyer_rub_per_xtr() -> Decimal:
    raw = (os.getenv("TELEGRAM_STARS_BUYER_RUB_PER_XTR") or "1.54905").strip()
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid TELEGRAM_STARS_BUYER_RUB_PER_XTR") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("TELEGRAM_STARS_BUYER_RUB_PER_XTR must be positive")
    return value


def telegram_stars_price(package_id: str | None) -> int:
    package = package_by_id(package_id)
    if telegram_stars_pricing_mode() == "buyer_parity":
        amount = int(
            (Decimal(package.price_rub) / telegram_stars_buyer_rub_per_xtr()).to_integral_value(rounding=ROUND_FLOOR)
        )
    else:
        raw = (os.getenv(_telegram_stars_env_key(package.package_id)) or "").strip()
        try:
            amount = int(raw) if raw else int(package.price_xtr)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Telegram Stars price for {package.package_id}") from exc
    if amount <= 0 or amount > 100_000:
        raise ValueError(f"Telegram Stars price out of range for {package.package_id}")
    return amount
