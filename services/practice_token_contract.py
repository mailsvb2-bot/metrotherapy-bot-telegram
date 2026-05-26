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
    PracticePackage("practice_start_7", "Start package", "7 practices.", 7, 1900, 10, True, ""),
    PracticePackage("practice_60", "Full route", "60 practices.", 60, 7900, 20, True, ""),
    PracticePackage("practice_antistress_60", "Anti-stress system", "60 practices plus video course.", 60, 12900, 30, True, ""),
    PracticePackage("practice_personal_month", "Personal month", "60 practices plus video course plus consultation request.", 60, 23000, 40, True, ""),
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


def normalize_delivery_mode(raw: str | None) -> str:
    value = (raw or "").strip().casefold().replace("ё", "е")
    aliases = {
        "": "single_daily",
        "1": "single_daily",
        "single": "single_daily",
        "single_daily": "single_daily",
        "morning": "morning_only",
        "morning_only": "morning_only",
        "evening": "evening_only",
        "evening_only": "evening_only",
        "both": "both",
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
