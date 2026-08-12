from __future__ import annotations

import json
from pathlib import Path

from services import funnel_analytics
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


def test_money_funnel_recovers_historical_listen_from_audio_timeline() -> None:
    """Old mood:done confirmations remain valid evidence after telemetry repair.

    Before the forward fix, the canonical audio timeline recorded the real demo
    confirmation but the commercial events table could miss demo_ack/audio_listened.
    The strict money funnel must recover that history without writing synthetic
    analytics rows or counting paid full-series confirmations.
    """

    user_id = -955003
    start = "2099-10-01T00:00:00+00:00"
    end = "2099-10-02T00:00:00+00:00"
    before = funnel_analytics._strict_money_counts(start, end)
    raw_before = funnel_analytics._counts(["demo_ack", "audio_listened"], start, end)

    with db() as conn:
        conn.executemany(
            "INSERT INTO events(user_id, name, meta, created_at) VALUES(?,?,?,?)",
            [
                (user_id, "demo_sent", "{}", "2099-10-01T10:00:00+00:00"),
                (user_id, "view_tariffs", "{}", "2099-10-01T10:02:00+00:00"),
                (user_id, "payment_started", "{}", "2099-10-01T10:03:00+00:00"),
            ],
        )
        conn.execute(
            """
            INSERT INTO user_audio_timeline(
                user_id, sequence_key, event_type, anchor, title,
                platform, token, meta_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """.strip(),
            (
                user_id,
                "demo",
                "manual_confirmed",
                17,
                "Historical demo",
                "telegram",
                None,
                "{}",
                "2099-10-01T10:01:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO payment_token_grants(
                provider, provider_payment_id, user_id, package_id,
                tokens_granted, ledger_id, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """.strip(),
            (
                "yookassa",
                "historical-listen-paid-955003",
                user_id,
                "practice_60",
                60,
                None,
                "2099-10-01T10:04:00+00:00",
            ),
        )
        conn.commit()

    current = funnel_analytics._strict_money_counts(start, end)
    for key in ("demo", "listened", "offer", "checkout", "paid", "paid_total"):
        assert current[key] == before[key] + 1

    # The fallback is read-only: it recovers truth from the existing canonical
    # timeline instead of manufacturing a historical analytics event.
    raw_after = funnel_analytics._counts(["demo_ack", "audio_listened"], start, end)
    assert raw_after == raw_before
