from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PracticePackage:
    package_id: str
    title: str
    description: str
    tokens: int
    price_rub: int
    sort_order: int = 100


DEFAULT_PRACTICE_PACKAGES: tuple[PracticePackage, ...] = (
    PracticePackage("practice_5", "5 практик", "Мягкий пробный пакет", 5, 990, 10),
    PracticePackage("practice_20", "20 практик", "Базовый маршрут", 20, 3490, 20),
    PracticePackage("practice_60", "60 практик", "Месяц утро + вечер", 60, 7900, 30),
)

VALID_DELIVERY_MODES = frozenset({
    "single_daily",
    "morning_only",
    "evening_only",
    "both",
    "paused",
})


def normalize_delivery_mode(raw: str | None) -> str:
    value = (raw or "").strip().casefold().replace("ё", "е")
    aliases = {
        "": "single_daily",
        "1": "single_daily",
        "single": "single_daily",
        "single_daily": "single_daily",
        "утро": "morning_only",
        "только утро": "morning_only",
        "morning": "morning_only",
        "morning_only": "morning_only",
        "вечер": "evening_only",
        "только вечер": "evening_only",
        "evening": "evening_only",
        "evening_only": "evening_only",
        "оба": "both",
        "both": "both",
        "утро вечер": "both",
        "утро+вечер": "both",
        "утро + вечер": "both",
        "пауза": "paused",
        "pause": "paused",
        "paused": "paused",
    }
    return aliases.get(value, "single_daily")


def daily_practice_cost(mode: str) -> int:
    normalized = normalize_delivery_mode(mode)
    if normalized == "both":
        return 2
    if normalized == "paused":
        return 0
    return 1


def package_by_id(package_id: str | None) -> PracticePackage:
    wanted = (package_id or "practice_20").strip() or "practice_20"
    for package in DEFAULT_PRACTICE_PACKAGES:
        if package.package_id == wanted:
            return package
    raise ValueError(f"Unknown practice package: {wanted}")
