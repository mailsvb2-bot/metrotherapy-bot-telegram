from __future__ import annotations

import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.db import db
from services.db.runtime import CONFIG
from services.messenger.delivery_outbox import claim_due_deliveries, mark_delivery_sent, persist_reply_bundle
from services.messenger.text_ui import MessengerReply
from services.messenger.webhook_dedupe import claim_inbound_event
from services.schema import init_db


def _cleanup(platform: str, event_key: str) -> None:
    with db() as conn:
        conn.execute(
            "DELETE FROM messenger_delivery_outbox WHERE platform=? AND event_key=?",
            (platform, event_key),
        )
        conn.execute(
            "DELETE FROM messenger_webhook_events WHERE platform=? AND event_key=?",
            (platform, event_key),
        )


def main() -> int:
    if not CONFIG.uses_postgres:
        raise SystemExit("POSTGRES_MESSENGER_OUTBOX_FAILED: METRO_DB_ENGINE=postgres is required")
    if not (os.getenv("DATABASE_URL") or "").strip():
        raise SystemExit("POSTGRES_MESSENGER_OUTBOX_FAILED: DATABASE_URL is required")

    init_db()
    platform = "vk"
    event_key = f"postgres-ci-{uuid.uuid4().hex}"
    user_id = -920_000_131
    payload = {"type": "message_new", "object": {"event_id": event_key}}
    try:
        assert claim_inbound_event(platform, event_key, payload) is True
        assert persist_reply_bundle(
            platform=platform,
            external_user_id="postgres-ci-user",
            canonical_user_id=user_id,
            event_key=event_key,
            replies=[MessengerReply(text="postgres durable delivery")],
            action="ci_probe",
        ) is True
        assert persist_reply_bundle(
            platform=platform,
            external_user_id="postgres-ci-user",
            canonical_user_id=user_id,
            event_key=event_key,
            replies=[MessengerReply(text="must not duplicate")],
            action="ci_probe",
        ) is False

        with ThreadPoolExecutor(max_workers=4) as executor:
            batches = list(executor.map(lambda _: claim_due_deliveries(limit=1), range(4)))
        claimed = [item for batch in batches for item in batch if item.event_key == event_key]
        assert len(claimed) == 1, f"expected one claim, got {len(claimed)}"
        mark_delivery_sent(claimed[0])

        with db() as conn:
            row = conn.execute(
                "SELECT status, attempts, sent_at FROM messenger_delivery_outbox WHERE platform=? AND event_key=?",
                (platform, event_key),
            ).fetchone()
        assert row is not None
        assert str(row["status"]) == "sent"
        assert int(row["attempts"] or 0) == 0
        assert str(row["sent_at"] or "")

        print(
            json.dumps(
                {
                    "ok": True,
                    "probe": "postgres_messenger_outbox",
                    "claims": len(claimed),
                    "status": str(row["status"]),
                },
                sort_keys=True,
            )
        )
        print("POSTGRES_MESSENGER_OUTBOX_OK")
        return 0
    finally:
        _cleanup(platform, event_key)


if __name__ == "__main__":
    raise SystemExit(main())
