from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from core.time_utils import utc_now

from services.db import db

MAX_PRICE_RUB = 1_000_000

log = logging.getLogger(__name__)


def write_tariffs_file(_prices: dict[str, int]) -> None:
    """Файл тарифов больше не используется."""
    log.info("write_tariffs_file: ignored (tariffs file is deprecated)")
def sync_tariffs_to_db(_conn=None) -> bool:
    """Файл тарифов больше не используется."""
    return False
