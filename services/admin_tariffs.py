from __future__ import annotations

import io
import logging
import os
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from services.db import db

log = logging.getLogger(__name__)

_MPL_READY = False


def _ensure_mpl() -> None:
    """Ленивая инициализация matplotlib для админских графиков."""

    global _MPL_READY
    if _MPL_READY:
        return
    mpl_dir = (Path(__file__).resolve().parents[1] / "data" / "mplcache").resolve()
    try:
        mpl_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("Не удалось создать каталог mplcache")
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
    try:
        import matplotlib

        matplotlib.use("Agg")
    except OSError:
        log.exception("Не удалось инициализировать matplotlib")
        raise
    _MPL_READY = True


def _plt():
    _ensure_mpl()
    import matplotlib.pyplot as plt  # type: ignore

    return plt


def _parse_plan_code_from_payload(payload: str | None) -> str | None:
    """payload формата sub:<plan_code>:<days> (или совместимые варианты)."""
    if not payload:
        return None
    payload = str(payload)
    if not payload.startswith("sub:"):
        return None
    parts = payload.split(":")
    if len(parts) >= 2:
        code = parts[1].strip()
        return code or None
    return None


def _to_day(ts: str | None) -> str | None:
    if not ts:
        return None
    # ожидаем ISO 8601 или "YYYY-MM-DD ..."
    s = str(ts)
    return s[:10] if len(s) >= 10 else None


def _day_to_dt(day: str) -> datetime:
    """Преобразует 'YYYY-MM-DD' в datetime для корректной оси X matplotlib."""
    return datetime.strptime(day, "%Y-%m-%d")


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    try:
        if hasattr(row, "keys"):
            return row[key]
        return row[index]
    except (KeyError, IndexError, TypeError, OSError):
        return default


def _safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def build_tariff_dynamics_images(plans: list[dict]) -> list[tuple[str, bytes]]:
    """Строит графики по каждому тарифу: цена (левая ось) + количество оплат (правая ось).

    Возвращает список (caption, png_bytes).
    """
    plt = _plt()
    plan_map: dict[str, dict] = {}
    for raw_plan in plans or []:
        if not isinstance(raw_plan, dict):
            continue
        code = str(raw_plan.get("code") or "").strip()
        if code:
            plan_map[code] = raw_plan

    # 1) платежи
    payments_by_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with db() as conn:
        # payload + paid_at или created_at
        # База у пользователей может быть старой версии без paid_at — делаем безопасно.
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()]
        except (sqlite3.Error, OSError, TypeError, IndexError):
            cols = []
        try:
            if "paid_at" in cols:
                rows = conn.execute(
                    "SELECT payload, created_at, paid_at FROM payments ORDER BY id DESC LIMIT 50000"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload, created_at FROM payments ORDER BY id DESC LIMIT 50000"
                ).fetchall()
        except (sqlite3.Error, OSError):
            log.exception("Не удалось прочитать платежи для динамики тарифов")
            rows = []

        for row in rows or []:
            payload = _row_value(row, "payload", 0)
            created_at = _row_value(row, "created_at", 1)
            paid_at = _row_value(row, "paid_at", 2)
            code = _parse_plan_code_from_payload(payload)
            if not code:
                continue
            day = _to_day(paid_at) or _to_day(created_at)
            if not day:
                continue
            payments_by_day[code][day] += 1

    # 2) история цен
    price_hist: dict[str, list[tuple[str, int | None, int]]] = defaultdict(list)
    with db() as conn:
        try:
            rows = conn.execute(
                "SELECT plan_code, old_price, new_price, changed_at_utc "
                "FROM plan_price_history ORDER BY changed_at_utc ASC"
            ).fetchall()
        except (sqlite3.Error, OSError):
            rows = []
        for row in rows or []:
            code = str(_row_value(row, "plan_code", 0, "") or "").strip()
            old_price = _safe_int(_row_value(row, "old_price", 1))
            new_price = _safe_int(_row_value(row, "new_price", 2))
            day = _to_day(_row_value(row, "changed_at_utc", 3))
            if not code or not day or new_price is None:
                continue
            price_hist[code].append((day, old_price, new_price))

    out: list[tuple[str, bytes]] = []

    # планы могут быть не только активные — берём те, что встречаются в оплатах/истории,
    # плюс активные из plan_map
    codes: set[str] = set(plan_map.keys()) | set(payments_by_day.keys()) | set(price_hist.keys())

    for code in sorted(codes):
        plan = plan_map.get(code, {})
        title = str(plan.get("title") or code)
        current_price = _safe_int(plan.get("price"))

        # собрать календарь дней
        days: set[str] = set(payments_by_day.get(code, {}).keys())
        for day, _old, _new in price_hist.get(code, []):
            days.add(day)
        if not days:
            # совсем нет данных — пропускаем
            continue

        all_days = sorted(days)

        # сформировать ряд цен как step (последнее значение)
        price_by_day: dict[str, int] = {}
        last_price: int | None = current_price

        # если есть история, пытаемся начать с old_price первой записи
        hist = price_hist.get(code, [])
        if hist:
            _first_day, first_old, _first_new = hist[0]
            if first_old is not None:
                last_price = first_old

        # применяем изменения по дням
        changes_by_day = {day: new for day, _old, new in hist}

        for day in all_days:
            if day in changes_by_day:
                last_price = changes_by_day[day]
            if last_price is None:
                # fallback: если цена повреждена или отсутствует, график остаётся доступным
                last_price = 0
            price_by_day[day] = last_price

        pay_by_day = payments_by_day.get(code, {})
        pay_series = [int(pay_by_day.get(day, 0)) for day in all_days]
        price_series = [int(price_by_day.get(day, 0)) for day in all_days]

        # рисуем (ось X как даты, чтобы не было категориальных предупреждений)
        import matplotlib.dates as mdates  # type: ignore

        x = [_day_to_dt(day) for day in all_days]

        fig, ax1 = plt.subplots(figsize=(10, 4))
        try:
            ax1.set_title(f"{title} ({code})")
            ax1.set_xlabel("Дата")
            ax1.set_ylabel("Цена, ₽")
            ax1.plot(x, price_series, marker="o")

            ax2 = ax1.twinx()
            ax2.set_ylabel("Оплаты, шт")
            ax2.bar(x, pay_series, alpha=0.3)

            ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
            ax1.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax1.xaxis.get_major_locator()))
            fig.autofmt_xdate(rotation=45)

            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=140)
            out.append((f"{title} — цена и оплаты", buf.getvalue()))
        finally:
            plt.close(fig)

    return out
