from __future__ import annotations

import logging
import compileall
import os
import re
from pathlib import Path
from typing import Iterable

import sqlite3

from services.db import get_connection, DB_PATH
from core.paths import ROOT as PROJECT_ROOT

log = logging.getLogger(__name__)


class ValidationError(RuntimeError):
    pass
