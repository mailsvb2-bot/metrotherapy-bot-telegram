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
    public: bool = True
    badge: str = ""


DEFAULT_PRACTICE_PACKAGES: tuple[PracticePackage, ...] = (
    PracticePackage("practice_start_7", "Стартовый пакет", "7 практик. Мягкий вход и проверка формата.", 7, 1900, 10, True, ""),
    PracticePackage("practice_60", "Полный маршрут", "60 практик. Базовый месячный маршрут.", 60, 7900, 20, True, ""),
    PracticePackage("practice_antistress_60", "Антистресс-система", "60 практик + доступ к видеокурсу.", 60, 12900, 30, True, ""),
    PracticePackage("practice_personal_month", "Персональный месяц", "60 практик + видеокурс + заявка на консультацию.", 60, 23000, 40, True, ""),
)

LEGACY_PRACTICE_PACKAGES: tuple[PracticePackage, ...] = (
    PracticePackage("practice_5", "5 practices", "Legacy trial package", 5, 990, 900, False, ""),
    PracticePackage("practice_20", "20 practices", "Legacy base route", 20, 3490, 910, False, ""),
)

ALL_PRACTICE_PACKAGES: tuple[PracticePackage, ...] = DEFAULT_PRACTICE_PACKAGES + LEGACY_PRACTICE_PACKAGES

VALID_DELIVERY_MODES = frozenset({
    "single_daily",
    "morning_only",
    "evening_only",
    "both",
    "paused",
})


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
