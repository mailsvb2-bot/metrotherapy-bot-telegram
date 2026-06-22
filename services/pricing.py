from __future__ import annotations

"""Pricing/tariffs helpers.

Implementation split into smaller modules to reduce regression risk.
Public API remains in this module for backward compatibility.
"""

from services.pricing_read import _norm_title, suggest_plan_titles, read_plans
from services.pricing_update import (
    set_plan_price,
    set_plan_price_by_code,
    set_plan_price_by_title,
    set_plan_prices_by_titles,
    set_plan_prices_by_titles_verbose,
)
from services.pricing_sync import write_tariffs_file, sync_tariffs_to_db

__all__ = [
    "_norm_title",
    "suggest_plan_titles",
    "read_plans",
    "set_plan_price",
    "set_plan_price_by_code",
    "set_plan_price_by_title",
    "set_plan_prices_by_titles",
    "set_plan_prices_by_titles_verbose",
    "write_tariffs_file",
    "sync_tariffs_to_db",
]
