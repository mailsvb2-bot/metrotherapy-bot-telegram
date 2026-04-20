from __future__ import annotations


import os
from pathlib import Path

# Единый источник истины по путям.
# Никаких зависимостей от "текущей папки запуска".

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "data"
DB_ENGINE = (os.getenv("METRO_DB_ENGINE") or ("postgres" if os.getenv("DATABASE_URL") else "sqlite")).strip().lower()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
DB_PATH = Path(os.getenv("METRO_DB_PATH") or (DATA_DIR / "data.db"))

AUDIO_DIR = ROOT / "audio"
DEMO_DIR = AUDIO_DIR / "demo"
FULL_DIR = AUDIO_DIR / "full"

LOGS_DIR = ROOT / "logs"
