from __future__ import annotations

import inspect

from core.callbacks import ADMIN_TARIFFS
from handlers import admin_inline_reports
from handlers.admin_reports import ad_links, money_clients
from handlers.text_input_parts import admin_users as admin_user_inputs
from keyboards.inline import kb_admin_ad_links, kb_admin_menu, kb_admin_money_card, kb_admin_money_payments


_DIRECT_ADMIN_CALLBACKS = {
    "admin:menu",
    "admin:add_admin",
    "admin:roles:menu",
    "admin:perms",
    ADMIN_TARIFFS,
}

_USER_CALLBACKS = {"admin:users:today", "admin:user:card"}
_COPY_CALLBACKS = {"admin:copy:menu", "admin:ai:prices"}
_REPORT_CALLBACKS = set(admin_inline_reports._HANDLERS.keys())


def _callbacks(markup) -> list[str]:
    out: list[str] = []
    for row in markup.inline_keyboard:
        for btn in row:
            data = getattr(btn, "callback_data", None)
            if data:
                out.append(str(data))
    return out


def _is_known_admin_route(data: str) -> bool:
    return (
        data in _DIRECT_ADMIN_CALLBACKS
        or data in _USER_CALLBACKS
        or data in _COPY_CALLBACKS
        or data in _REPORT_CALLBACKS
        or data.startswith("admin:roles:")
        or data.startswith("admin:perms:")
        or data.startswith("admin:tariffs:")
        or data.startswith("admin:money:payment:")
        or data.startswith("admin:adlinks:create:")
    )


def test_visible_admin_menu_buttons_have_routes():
    missing = [data for data in _callbacks(kb_admin_menu()) if data.startswith("admin:") and not _is_known_admin_route(data)]

    assert missing == []


def test_payments_button_opens_real_payment_list():
    assert admin_inline_reports._HANDLERS["admin:conversion"] is money_clients.run


def test_ad_links_button_opens_real_ad_links_screen():
    assert admin_inline_reports._HANDLERS["admin:adlinks"] is ad_links.run


def test_ad_links_nested_buttons_have_routes():
    callbacks = _callbacks(kb_admin_ad_links())
    missing = [data for data in callbacks if data.startswith("admin:") and not _is_known_admin_route(data)]

    assert missing == []


def test_money_cockpit_nested_buttons_have_routes():
    callbacks = _callbacks(kb_admin_money_payments([1, 2, 3], "today")) + _callbacks(kb_admin_money_card(1))
    missing = [data for data in callbacks if data.startswith("admin:") and not _is_known_admin_route(data)]

    assert missing == []


def test_role_admin_user_card_input_uses_canonical_admin_check():
    source = inspect.getsource(admin_user_inputs.msg_admin_user_card)

    assert "is_admin(admin_id)" in source
    assert "settings.admin_id_list" not in source
