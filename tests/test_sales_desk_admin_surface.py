from __future__ import annotations

import re
from pathlib import Path

from services.admin_permissions import (
    SALES_DESK_PERMISSION,
    SALES_MESSAGE_PERMISSION,
    SALES_WRITE_PERMISSION,
)

ROOT = Path(__file__).resolve().parents[1]


def _surface_text() -> str:
    paths = (
        ROOT / "handlers" / "admin_reports" / "sales_desk.py",
        ROOT / "handlers" / "admin_reports" / "sales_desk_ui.py",
        ROOT / "handlers" / "admin_reports" / "sales_desk_inputs.py",
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_sales_desk_permissions_are_explicit_and_fail_closed() -> None:
    admin = (ROOT / "handlers" / "admin_inline.py").read_text(encoding="utf-8")
    surface = _surface_text()

    assert SALES_DESK_PERMISSION == "admin:sales"
    assert SALES_WRITE_PERMISSION == "admin:sales:write"
    assert SALES_MESSAGE_PERMISSION == "admin:sales:message"
    assert 'callback_data="admin:sales"' in admin
    assert "SALES_WRITE_PERMISSION in ctx.allowed_perms" in surface
    assert "SALES_MESSAGE_PERMISSION in ctx.allowed_perms" in surface
    assert "ctx.allowed_perms is not None" in surface
    assert "if ctx.is_superadmin" in surface


def test_manual_message_is_audited_and_never_automatic() -> None:
    surface = _surface_text()
    contact = (ROOT / "services" / "sales_desk_contact.py").read_text(
        encoding="utf-8"
    )
    migration = (
        ROOT / "services" / "migrations" / "sales_desk_v5.py"
    ).read_text(encoding="utf-8")

    assert "prepare_sales_message" in surface
    assert "msg.bot.send_message" in surface
    assert "sales_outbound_messages" in contact
    assert "outbound_prepared" in contact
    assert "outbound_sent" in contact
    assert 'event_type=f"outbound_{status}"' in contact
    assert "status IN ('prepared','sent','failed','uncertain')" in migration

    forbidden_automation = (
        "scheduler.add_job",
        "create_task(send_sales",
        "broadcast_sales",
        "flush_sales_outbound",
        "dispatch_sales_outbound",
    )
    combined = surface + "\n" + contact
    for token in forbidden_automation:
        assert token not in combined


def test_sales_desk_does_not_mutate_existing_business_contexts() -> None:
    paths = [
        ROOT / "services" / "sales_desk.py",
        ROOT / "services" / "sales_desk_sync.py",
        ROOT / "services" / "sales_desk_repository.py",
        ROOT / "services" / "sales_desk_contact.py",
        ROOT / "handlers" / "admin_reports" / "sales_desk.py",
        ROOT / "handlers" / "admin_reports" / "sales_desk_inputs.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    forbidden = (
        "UPDATE growth_apply_requests",
        "UPDATE payments",
        "UPDATE users",
        "UPDATE subscriptions",
        "UPDATE practice_",
        "DELETE FROM payments",
        "DELETE FROM users",
        "DELETE FROM subscriptions",
    )
    for token in forbidden:
        assert token not in combined


def test_all_sales_callbacks_fit_telegram_limit() -> None:
    surface = _surface_text()
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
    registry = (ROOT / "services" / "migrations" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert registry.count("_apply_sales_desk_v5") == 2


def test_sales_report_handler_is_not_a_god_module() -> None:
    handler = ROOT / "handlers" / "admin_reports" / "sales_desk.py"
    assert len(handler.read_text(encoding="utf-8").splitlines()) < 350
