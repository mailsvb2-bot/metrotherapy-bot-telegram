from __future__ import annotations
import logging

# Facade kept for backward-compatible imports.
from handlers.admin_tariffs.common import TariffsCtx
from handlers.admin_tariffs.ui import render_tariffs_menu, tariffs_history
from handlers.admin_tariffs.callbacks import tariffs_edit, tariffs_pick, tariffs_dynamics, handle_tariffs_callback
from handlers.admin_tariffs.inputs import admin_tariffs_input, admin_tariff_single_price_input

# re-export for backward compatibility
from handlers.admin_tariffs.ui import kb_tariffs_nav
