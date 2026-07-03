"""Compatibility module.

Some refactors/imports expect `keyboards.common`.
This module re-exports keyboard builders from `keyboards.inline` and `keyboards.reply`.
"""

from keyboards.inline import *
from keyboards.reply import *
