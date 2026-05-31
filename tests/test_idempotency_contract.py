from __future__ import annotations

from services.db import mark_delivery_once, unmark_delivery, was_delivered


def test_mark_delivery_once_accepts_legacy_single_key() -> None:
    user_id = 910001
    key = "legacy:delivery:key"

    unmark_delivery(user_id, key)

    assert mark_delivery_once(user_id, key) is True
    assert was_delivered(user_id, key) is True
    assert mark_delivery_once(user_id, key) is False

    unmark_delivery(user_id, key)
    assert was_delivered(user_id, key) is False


def test_mark_delivery_once_accepts_runtime_semantic_parts() -> None:
    user_id = 910002
    key_parts = ("work", "pre_score", "d:910002:2026-05-31:morning:pre_score")

    unmark_delivery(user_id, *key_parts)

    assert mark_delivery_once(user_id, *key_parts) is True
    assert was_delivered(user_id, *key_parts) is True
    assert mark_delivery_once(user_id, *key_parts) is False

    unmark_delivery(user_id, *key_parts)
    assert was_delivered(user_id, *key_parts) is False


def test_mark_delivery_once_rejects_empty_key() -> None:
    try:
        mark_delivery_once(910003, "", "  ")
    except ValueError as exc:
        assert "must not be empty" in str(exc)
    else:  # pragma: no cover - explicit regression guard
        raise AssertionError("empty idempotency key must be rejected")
