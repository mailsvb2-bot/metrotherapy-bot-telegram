from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_ROOT = Path(tempfile.gettempdir()) / "metrotherapy_pytest"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

# Pytest must never inherit production DB/messenger/webhook state from systemd/.env.
os.environ["APP_ENV"] = "test"
os.environ["LOAD_DOTENV"] = "0"
os.environ["METRO_DB_ENGINE"] = "sqlite"
os.environ["DATABASE_URL"] = ""
os.environ["METRO_DB_PATH"] = str(TEST_ROOT / f"pytest_{os.getpid()}.db")

os.environ.setdefault("BOT_TOKEN", "000000:TEST")
os.environ.setdefault("PAY_PROVIDER_TOKEN", "000000:TEST")

# Messenger/webhook defaults for deterministic unit tests.
os.environ["TELEGRAM_TRANSPORT"] = "polling"
os.environ["TELEGRAM_WEBHOOK_ENABLED"] = "0"
os.environ["MESSENGER_WEBHOOK_ENABLED"] = "0"

# Prevent real server integrations leaking into tests.
for name in (
    "MAX_BOT_TOKEN",
    "MAX_BOT_NAME",
    "MAX_BOT_LINK_BASE",
    "VK_GROUP_TOKEN",
    "VK_CONFIRMATION_TOKEN",
    "VK_SECRET",
    "VK_GROUP_ID",
    "MESSENGER_PUBLIC_BASE_URL",
    "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL",
    "TELEGRAM_WEBHOOK_SECRET_TOKEN",
):
    os.environ.pop(name, None)

# Tests that exercise messenger text entrypoints must use the same canonical
# schema bootstrap as application startup. Otherwise a fresh isolated pytest DB
# exists but has no users/events tables, and button-parity tests fail before
# reaching the messenger behavior under test.
from services.schema import init_db

init_db()
