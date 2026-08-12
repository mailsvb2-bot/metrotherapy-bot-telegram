from __future__ import annotations

import json
from pathlib import Path

from services.db import db
from services.messenger.audio_progress import (
    AudioProgressItem,
    confirm_pending_audio_delivery,
    mark_pending_audio_delivery,
)
from services.schema import init_db


def setup_module(module) -> None:
    init_db()


def _audio_listened_rows(user_id: int):
    with db() as conn:
        return conn.execute(
            """
            SELECT name, meta, created_at
            FROM events
            WHERE user_id=? AND name='audio_listened'
            ORDER BY id
            """.strip(),
            (int(user_id),),
        ).fetchall()


def test_demo_confirmation_emits_one_canonical_listen_event() -> None:
    user_id = -955001
    item = AudioProgressItem(
        ordinal=1,
        anchor=17,
        title="Demo telemetry",
        path=Path("audio/demo/demo-telemetry.opus"),
    )
    before = len(_audio_listened_rows(user_id))

    mark_pending_audio_delivery(
        user_id,
        item=item,
        platform="vk",
        token=None,
        sequence_key="demo",
    )
    confirmed = confirm_pending_audio_delivery(
        user_id,
        platform="vk",
        sequence_key="demo",
    )

    assert confirmed is not None
    assert confirmed.anchor == 17
    rows = _audio_listened_rows(user_id)
    assert len(rows) == before + 1
    payload = json.loads(str(rows[-1]["meta"] or "{}"))
    assert payload == {
        "sequence_key": "demo",
        "anchor": 17,
        "title": "Demo telemetry",
        "source": "manual_confirmed",
    }

    # Confirmation is state-driven: once the pending demo has been consumed,
    # a repeated button/text action cannot create duplicate funnel evidence.
    assert (
        confirm_pending_audio_delivery(
            user_id,
            platform="vk",
            sequence_key="demo",
        )
        is None
    )
    assert len(_audio_listened_rows(user_id)) == before + 1


def test_paid_full_series_confirmation_is_not_demo_listen_evidence() -> None:
    user_id = -955002
    item = AudioProgressItem(
        ordinal=1,
        anchor=18,
        title="Paid telemetry",
        path=Path("audio/full/paid-telemetry.opus"),
    )
    before = len(_audio_listened_rows(user_id))

    mark_pending_audio_delivery(
        user_id,
        item=item,
        platform="telegram",
        token=None,
    )
    confirmed = confirm_pending_audio_delivery(
        user_id,
        platform="telegram",
    )

    assert confirmed is not None
    assert confirmed.anchor == 18
    assert len(_audio_listened_rows(user_id)) == before
