from __future__ import annotations
import sqlite3
import logging


"""Графики прогресса (PNG) для Telegram.

Требования:
- Отправлять как изображение (photo) прямо в чат.
- 3 графика: работа, дом, общая динамика.
- Разные цвета линий (используем стандартные цвета matplotlib).
- Без сброса данных: строим по всей истории.
"""

import io
import hashlib
import time
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from config.settings import settings
from typing import Any

# Кеш PNG, чтобы кнопки ощущались мгновенными
_CHART_CACHE: dict[str, tuple[float, bytes]] = {}
_CHART_CACHE_TTL = 10 * 60  # 10 минут


def _chart_cache_key(prefix: str, kind_title: str, rows: list[dict[str, Any]]) -> str:
    tail = rows[-20:] if rows else []
    payload = repr((kind_title, len(rows), [(_rget(r, 'ts') or _rget(r, 'date'), _rget(r, 'pre'), _rget(r, 'post')) for r in tail]))
    h = hashlib.sha1(payload.encode('utf-8', errors='ignore')).hexdigest()
    return f"{prefix}:{h}"


# Убираем зависимость от домашней директории и прав доступа (Принцип B)
_MPL_READY = False


def _ensure_mpl() -> None:
    """Ленивая инициализация matplotlib.

    Matplotlib при первом импорте может долго строить font cache (10+ секунд).
    Чтобы бот не "тормозил" на первом пользовательском апдейте, не импортируем
    matplotlib на уровне модуля.
    """

    global _MPL_READY
    if _MPL_READY:
        return
    _mpl_dir = (Path(__file__).resolve().parents[1] / "data" / "mplcache").resolve()
    try:
        _mpl_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logging.getLogger(__name__).exception("Не удалось создать каталог mplcache")
    os.environ.setdefault("MPLCONFIGDIR", str(_mpl_dir))
    try:
        import matplotlib

        matplotlib.use("Agg")

        # Use matplotlib bundled font to avoid system font discovery surprises
        matplotlib.rcParams["font.family"] = "DejaVu Sans"
        matplotlib.rcParams["font.sans-serif"] = ["DejaVu Sans"]
        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)
        logging.getLogger("matplotlib.category").setLevel(logging.WARNING)
    except OSError:
        logging.getLogger(__name__).exception("Не удалось инициализировать matplotlib")
        raise
    _MPL_READY = True


def _plt():
    _ensure_mpl()
    import matplotlib.pyplot as plt  # type: ignore

    return plt

def _rget(r: Any, key: str, default: Any = None) -> Any:
    """Безопасно достаёт значение из dict / sqlite3.Row / любых mapping-подобных."""
    try:
        return r.get(key, default)  # type: ignore[attr-defined]
    except AttributeError:
        try:
            return r[key]  # type: ignore[index]
        except (TypeError, KeyError, IndexError):
            return default

def _to_points(rows: list[dict[str, Any]], key: str) -> list[float | None]:
    out: list[float | None] = []
    for r in rows:
        v = _rget(r, key)
        out.append(float(v) if v is not None else None)
    return out


def _x_labels(rows: list[dict[str, Any]], max_labels: int = 8) -> tuple[list[int], list[str]]:
    """Позиции и подписи оси X.

    Требование UX: время на графике должно соответствовать реальному локальному времени,
    которое выбрал пользователь (TIMEZONE). Используем created_at_utc -> local HH:MM.
    Устойчиво работает даже при пустых данных.
    """
    n = len(rows)
    if n == 0:
        return ([0], [""])

    def _fmt_time(r: dict[str, Any]) -> str:
        created = (_rget(r, "created") or "").strip()
        if not created:
            # fallback: дата
            return str(_rget(r, "day") or "")[5:]
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            dt = dt.astimezone(ZoneInfo(getattr(settings, "TIMEZONE", "Europe/Moscow")))
            return dt.strftime("%H:%M")
        except (ValueError, TypeError):
            return str(_rget(r, "day") or "")[5:]

    if n == 1:
        return ([0], [_fmt_time(rows[0])])

    step = max(1, n // max_labels)
    tick_pos = list(range(0, n, step))
    tick_lbl = [_fmt_time(rows[i]) for i in tick_pos]
    return tick_pos, tick_lbl


def plot_mood(kind_title: str, rows: list[dict[str, Any]]) -> bytes:
    plt = _plt()
    key = _chart_cache_key('mood', kind_title, rows)
    cached = _CHART_CACHE.get(key)
    if cached and (time.time() - cached[0] < _CHART_CACHE_TTL):
        return cached[1]

    """График по конкретному виду (работа/дом)."""
    fig = plt.figure(figsize=(8, 4.2), dpi=160)
    ax = fig.add_subplot(111)

    xs = list(range(len(rows)))
    pre = _to_points(rows, "pre")
    post = _to_points(rows, "post")

    # По запросу UX: "что было" — красным, "что стало" — синим
    ax.plot(xs, pre, marker="o", linewidth=1.6, label="что было", color="red")
    ax.plot(xs, post, marker="o", linewidth=1.6, label="что стало", color="blue")

    ax.set_title(kind_title, fontsize=16, fontweight='bold')
    ax.set_ylabel('самооценка', fontsize=12)
    ax.set_ylim(-10.5, 10.5)
    ax.grid(True, linewidth=0.5, alpha=0.5)
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], labels[::-1], loc='best', fontsize=11)

    tick_pos, tick_lbl = _x_labels(rows)
    ax.set_xticks(tick_pos)
    ax.set_xticklabels(tick_lbl, rotation=0)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    out = buf.getvalue()
    _CHART_CACHE[key] = (time.time(), out)
    return out


def plot_overall(rows_work: list[dict[str, Any]], rows_home: list[dict[str, Any]]) -> bytes:
    plt = _plt()
    key = _chart_cache_key('overall', 'overall', (rows_work or []) + (rows_home or []))
    cached = _CHART_CACHE.get(key)
    if cached and (time.time() - cached[0] < _CHART_CACHE_TTL):
        return cached[1]

    """Общая динамика: среднее состояние (pre/post) по всем сессиям."""

    # Соединяем серии по времени создания (упрощённо: по id/вставке сохраняется порядок)
    rows = (rows_work or []) + (rows_home or [])
    # сорт по created, если есть
    try:
        rows.sort(key=lambda r: _rget(r, "created") or "")
    except (TypeError, KeyError) as e:
        logging.getLogger(__name__).exception("Chart sorting error: %s", e)

    xs = list(range(len(rows)))
    avg: list[float | None] = []
    for r in rows:
        pre = _rget(r, "pre")
        post = _rget(r, "post")
        vals = [v for v in (pre, post) if v is not None]
        avg.append(float(sum(vals) / len(vals)) if vals else None)

    fig = plt.figure(figsize=(8, 4.2), dpi=160)
    ax = fig.add_subplot(111)
    ax.plot(xs, avg, marker="o", linewidth=1.8, label="среднее")
    ax.set_title('Общая динамика состояния', fontsize=16, fontweight='bold')
    ax.set_ylabel('самооценка', fontsize=12)
    ax.set_ylim(-10.5, 10.5)
    ax.grid(True, linewidth=0.5, alpha=0.5)
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], labels[::-1], loc='best', fontsize=11)

    tick_pos, tick_lbl = _x_labels(rows)
    ax.set_xticks(tick_pos)
    ax.set_xticklabels(tick_lbl, rotation=0)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    out = buf.getvalue()
    _CHART_CACHE[key] = (time.time(), out)
    return out


def plot_state_ratings(title: str, rows: list[dict[str, Any]]) -> bytes:
    """График "как я прямо сейчас" (оценки 1..10).

    rows: [{"created": ..., "rating": ...}]
    """
    plt = _plt()
    key = _chart_cache_key('state', title, rows)
    cached = _CHART_CACHE.get(key)
    if cached and (time.time() - cached[0] < _CHART_CACHE_TTL):
        return cached[1]

    fig = plt.figure(figsize=(8, 4.2), dpi=160)
    ax = fig.add_subplot(111)
    xs = list(range(len(rows)))
    ys: list[float | None] = []
    for r in rows:
        try:
            ys.append(float(_rget(r, 'rating')))
        except (TypeError, ValueError):
            ys.append(None)

    ax.plot(xs, ys, marker="o", linewidth=1.8, label="оценка")
    ax.set_title(title, fontsize=16, fontweight='bold')
    ax.set_ylabel('оценка', fontsize=12)
    ax.set_ylim(0.5, 10.5)
    ax.set_yticks(list(range(1, 11)))
    ax.grid(True, linewidth=0.5, alpha=0.5)

    tick_pos, tick_lbl = _x_labels(rows)
    ax.set_xticks(tick_pos)
    ax.set_xticklabels(tick_lbl, rotation=0)
    ax.legend(loc='best', fontsize=11)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    out = buf.getvalue()
    _CHART_CACHE[key] = (time.time(), out)
    return out


def plot_tariffs_dynamics(title: str, price_events: list[dict[str, Any]], payments_daily: list[dict[str, Any]]) -> bytes:
    """График для админки: изменения цен + оплаты.

    price_events: [{"created": iso, "new_price": int, "code": str}]
    payments_daily: [{"day": "YYYY-MM-DD", "cnt": int, "amount": int}]
    """
    plt = _plt()

    payload = repr((len(price_events), price_events[-20:], len(payments_daily), payments_daily[-20:]))
    key = f"tariffs_dyn:{hashlib.sha1(payload.encode('utf-8', errors='ignore')).hexdigest()}"
    cached = _CHART_CACHE.get(key)
    if cached and (time.time() - cached[0] < _CHART_CACHE_TTL):
        return cached[1]

    fig = plt.figure(figsize=(8.2, 4.4), dpi=160)
    ax1 = fig.add_subplot(111)

    # --- price change events ---
    xs1 = list(range(len(price_events)))
    ys1: list[float | None] = []
    for e in price_events:
        try:
            ys1.append(float(_rget(e, "new_price")))
        except (TypeError, ValueError):
            ys1.append(None)
    if price_events:
        ax1.plot(xs1, ys1, marker="o", linewidth=1.2, label="новая цена (события)")
    ax1.set_ylabel("цена, ₽")
    ax1.grid(True, linewidth=0.5, alpha=0.5)

    # X labels from events (time)
    if price_events:
        tick_pos, tick_lbl = _x_labels(price_events)
        ax1.set_xticks(tick_pos)
        ax1.set_xticklabels(tick_lbl, rotation=0)
    else:
        ax1.set_xticks([0])
        ax1.set_xticklabels([""])

    # --- payments per day (secondary axis) ---
    ax2 = ax1.twinx()
    xs2 = list(range(len(payments_daily)))
    ys2: list[float] = []
    for p in payments_daily:
        try:
            ys2.append(float(_rget(p, "cnt") or 0))
        except (TypeError, ValueError):
            ys2.append(0.0)
    if payments_daily:
        ax2.plot(xs2, ys2, marker="s", linewidth=1.2, label="оплат/день")
    ax2.set_ylabel("оплат/день")

    ax1.set_title(title, fontsize=15, fontweight="bold")
    # combined legend
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    if h1 or h2:
        ax1.legend(h1 + h2, l1 + l2, loc="best", fontsize=10)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    out = buf.getvalue()
    _CHART_CACHE[key] = (time.time(), out)
    return out
