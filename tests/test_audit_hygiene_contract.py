from __future__ import annotations

from pathlib import Path


def test_no_engine_backup_artifacts_are_tracked_or_present():
    assert not list(Path("core").glob("*.bak*"))


def test_text_ui_uses_canonical_package_payment_surface():
    source = Path("services/messenger/text_ui.py").read_text(encoding="utf-8")

    assert "package_payment_text" in source
    assert "gift_package_text" in source
    assert "kind=\"subscription\"" not in source
    assert "kind=\"gift\"" not in source
    assert "_payment_public_base_url(" not in source
    assert "_payment_url(" not in source


def test_public_tariff_ui_does_not_wire_legacy_db_plans():
    source = Path("services/payments/ui.py").read_text(encoding="utf-8")

    public_surface = source.split("def kb_legacy_db_tariffs", 1)[0]
    assert "get_active_plans(" not in public_surface
    assert "public_practice_packages()" in public_surface
