from __future__ import annotations

from core.time_utils import utc_now
from services.db import db


def _coerce_plan_id(scope: str, days: int, plan_id: int | None) -> int | None:
    if plan_id is not None:
        try:
            return int(plan_id)
        except (TypeError, ValueError):
            return None
    try:
        from services.plans import get_plan_by_scope_days

        plan = get_plan_by_scope_days(str(scope), int(days))
        if not plan:
            return None
        return int(plan.get("id") or plan.get("plan_id") or 0) or None
    except (TypeError, ValueError, KeyError):
        return None


def set_plan(
    user_id: int,
    scope: str,
    days: int,
    title: str,
    price: int | None,
    plan_code: str,
    plan_id: int | None = None,
):
    """Store selected plan.

    IMPORTANT:
    - The *source of truth* for price/title/scope/days is the `plans` table.
    - `selected_plan` is only a UX helper to remember the choice.
    - Legacy callers that omit plan_id are normalized by resolving a canonical
      plan pointer from scope+days.
    """
    pid = _coerce_plan_id(scope, int(days), plan_id)

    if pid is not None:
        scope = str(scope)
        days = int(days)
        title = ""
        price = None
        plan_code = ""

    with db() as conn:
        conn.execute(
            """
            INSERT INTO selected_plan(user_id, plan_id, scope, days, title, price, plan_code, chosen_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan_id=excluded.plan_id,
                scope=excluded.scope,
                days=excluded.days,
                title=excluded.title,
                price=excluded.price,
                plan_code=excluded.plan_code,
                chosen_at=excluded.chosen_at
            """,
            (
                int(user_id),
                pid,
                scope,
                int(days),
                title,
                int(price) if price is not None else None,
                plan_code,
                utc_now().replace(microsecond=0).isoformat(),
            ),
        )


def get_plan(user_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM selected_plan WHERE user_id=?", (int(user_id),)).fetchone()
    if not row:
        return None
    data = dict(row)
    pid = data.get("plan_id")
    try:
        pid_i = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid_i = None
    if pid_i is not None:
        from services.plans import get_plan_by_id

        plan = get_plan_by_id(pid_i)
        if plan:
            data["title"] = str(plan.get("title") or "")
            data["price"] = int(plan.get("price") or 0)
            data["plan_code"] = str(plan.get("code") or plan.get("plan_code") or "")
            data["scope"] = str(plan.get("scope") or data.get("scope") or "")
            data["days"] = int(plan.get("days") or data.get("days") or 0)
    return data


def clear_plan(user_id: int):
    with db() as conn:
        conn.execute("DELETE FROM selected_plan WHERE user_id=?", (int(user_id),))


def get_plan_id(user_id: int) -> int | None:
    plan = get_plan(user_id)
    if not plan:
        return None
    pid = plan.get("plan_id")
    try:
        return int(pid) if pid is not None else None
    except (TypeError, ValueError):
        return None
