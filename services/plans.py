import logging

from services.db import db

log = logging.getLogger(__name__)


def _row_get(row, key: str, index: int, default=None):
    """Read DB rows safely across sqlite Row, psycopg dict_row, dict, and tuples."""
    if row is None:
        return default
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, TypeError, IndexError):
            pass
    try:
        return row[index]
    except (TypeError, IndexError, KeyError):
        return default


def _parse_int(value, *, field: str, plan_id=None, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    raw = str(value).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        log.warning(
            "Invalid integer plan field",
            extra={"field": field, "raw_value": repr(value), "plan_id": plan_id},
            exc_info=True,
        )
        return default


def _row_to_plan(row) -> dict:
    plan_id = _row_get(row, "id", 0)
    code = _row_get(row, "code", 1)
    title = _row_get(row, "title", 2)
    plan_type = _row_get(row, "plan_type", 3)
    touches = _row_get(row, "touches", 4)
    price = _row_get(row, "price", 5)
    is_active = _row_get(row, "is_active", 6)

    scope = plan_type  # historical name in the project
    days = _parse_int(touches, field="touches", plan_id=plan_id)
    # Цена в проекте хранится в рублях.
    # В старых БД могли оказаться цены в копейках (например, 99000 вместо 990).
    # Эвристика: если значение >= 100000 и делится на 100 — считаем, что это копейки.
    try:
        price_int = int(price)
    except (TypeError, ValueError):
        # Не маскируем странные данные: цена=0 может сломать платежи/UI.
        log.warning("Invalid plan price value", extra={"raw_price": repr(price), "plan_id": plan_id, "code": str(code)}, exc_info=True)
        price_int = 0
    if price_int >= 50000 and price_int % 100 == 0:
        price_int = price_int // 100
    return {
        "id": _parse_int(plan_id, field="id", plan_id=plan_id),
        # В проекте исторически встречаются оба ключа:
        # - code (в таблице plans)
        # - plan_code (в selected_plan/подарках/платежах)
        # Чтобы не ловить KeyError при смене реализаций, держим оба.
        "code": str(code),
        "plan_code": str(code),
        "title": str(title),
        "scope": str(scope),
        "days": days,
        "price": int(price_int),
        "is_active": bool(is_active),
    }


def get_active_plans() -> list[dict]:
    """Возвращает список активных планов (цены берём из БД)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, code, title, COALESCE(NULLIF(plan_type,''), scope) AS plan_type, COALESCE(touches, CASE WHEN days >= 20 THEN 20 ELSE 5 END) AS touches, price, is_active "
            "FROM plans WHERE is_active=1 ORDER BY plan_type, touches"
        ).fetchall()
        return [_row_to_plan(r) for r in rows]


def get_plans(*, include_inactive: bool = True) -> list[dict]:
    """Возвращает планы.

    include_inactive=True → все планы (для админки)
    include_inactive=False → только активные
    """
    with db() as conn:
        where = "" if include_inactive else "WHERE is_active=1"
        rows = conn.execute(
            "SELECT id, code, title, COALESCE(NULLIF(plan_type,''), scope) AS plan_type, "
            "COALESCE(touches, CASE WHEN days >= 20 THEN 20 ELSE 5 END) AS touches, "
            f"price, is_active FROM plans {where} ORDER BY is_active DESC, plan_type, touches"
        ).fetchall()
        return [_row_to_plan(r) for r in rows]


def get_plan_by_id(plan_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id, code, title, COALESCE(NULLIF(plan_type,''), scope) AS plan_type, COALESCE(touches, CASE WHEN days >= 20 THEN 20 ELSE 5 END) AS touches, price, is_active FROM plans WHERE id=?",
            (int(plan_id),),
        ).fetchone()
        return _row_to_plan(row) if row else None


def get_plan_by_scope_days(scope: str, days: int) -> dict | None:
    """Совместимость со старым кодом: ищем по plan_type+touches."""
    with db() as conn:
        row = conn.execute(
            "SELECT id, code, title, COALESCE(NULLIF(plan_type,''), scope) AS plan_type, COALESCE(touches, CASE WHEN days >= 20 THEN 20 ELSE 5 END) AS touches, price, is_active "
            "FROM plans WHERE plan_type=? AND touches=?",
            (scope, int(days)),
        ).fetchone()
        return _row_to_plan(row) if row else None
