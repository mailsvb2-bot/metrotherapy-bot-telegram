from __future__ import annotations

import re
from pathlib import Path

from services.admin_permissions import SALES_DESK_PERMISSION, SALES_WRITE_PERMISSION

ROOT = Path(__file__).resolve().parents[1]


def test_sales_desk_is_visible_but_write_permission_is_explicit() -> None:
    permissions = (ROOT / "services" / "admin_permissions.py").read_text(encoding="utf-8")
    admin = (ROOT / "handlers" / "admin_inline.py").read_text(encoding="utf-8")
    surface = (ROOT / "handlers" / "admin_reports" / "sales_desk.py").read_text(encoding="utf-8")

    assert SALES_DESK_PERMISSION == "admin:sales"
    assert SALES_WRITE_PERMISSION == "admin:sales:write"
    assert 'callback_data="admin:sales"' in admin
    assert "ctx.allowed_perms is not None and SALES_WRITE_PERMISSION in ctx.allowed_perms" in surface
    assert "if ctx.is_superadmin" in surface


def test_sales_desk_does_not_send_customer_messages_or_mutate_growth_apply() -> None:
    surface = (ROOT / "handlers" / "admin_reports" / "sales_desk.py").read_text(encoding="utf-8")
    service = (ROOT / "services" / "sales_desk.py").read_text(encoding="utf-8")

    forbidden = (
        "bot.send_message",
        "payment_url",
        "dispatch_allowed=1",
        "UPDATE growth_apply_requests",
        "UPDATE payments",
        "UPDATE users",
        "UPDATE subscriptions",
        "UPDATE practice_",
    )
    combined = surface + "\n" + service
    for token in forbidden:
        assert token not in combined


def test_all_sales_callbacks_fit_telegram_limit() -> None:
    surface = (ROOT / "handlers" / "admin_reports" / "sales_desk.py").read_text(encoding="utf-8")
    literals = re.findall(r'callback_data=(?:f)?"([^"]+)"', surface)
    assert literals
    for callback in literals:
        expanded = (
            callback.replace("{lead_id}", "9223372036854775807")
            .replace("{target}", "qualified")
            .replace("{key}", "unassigned")
            .replace("{selected}", "unassigned")
        )
        assert len(expanded.encode("utf-8")) <= 64, expanded


def test_sales_migration_is_registered_once() -> None:
    registry = (ROOT / "services" / "migrations" / "__init__.py").read_text(encoding="utf-8")
    assert registry.count("_apply_sales_desk_v5") == 2
