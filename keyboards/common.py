"""Compatibility module.

Some refactors/imports expect `keyboards.common`.
This module re-exports keyboard builders from `keyboards.inline` and `keyboards.reply`.
"""

from keyboards.inline import *  # noqa: F401,F403
from keyboards.reply import *   # noqa: F401,F403
