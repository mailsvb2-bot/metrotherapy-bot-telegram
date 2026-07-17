from __future__ import annotations

import uuid

from services import gift_claims
from services.db import db
from services.practice_tokens import get_wallet


def _paid_gift(*, buyer_id: int) -> str:
    token = gift_claims.create_gift_checkout_token(
        buyer_user_id=buyer_id,
        package_id="practice_start_7",
        source_platform="telegram",
    )
    gift_claims.mark_gift_paid(
        gift_token=token,
        buyer_user_id=buyer_id,
        package_id="practice_start_7",
        provider="test",
        provider_payment_id=f"gift-race-{uuid.uuid4().hex}",
        source_platform="telegram",
    )
    return token


def test_gift_is_pinned_to_winner_before_any_grant_side_effect(monkeypatch) -> None:
    buyer_id = 793001
    winner_id = 793002
    loser_id = 793003
    token = _paid_gift(buyer_id=buyer_id)
    original_grant = gift_claims.grant_tokens_for_payment
    nested: dict[str, gift_claims.GiftClaimResult] = {}

    def grant_with_competing_claim(**kwargs):
        nested["loser"] = gift_claims.claim_gift_token(
            gift_token=token,
            recipient_user_id=loser_id,
            platform="max",
        )
        return original_grant(**kwargs)

    monkeypatch.setattr(gift_claims, "grant_tokens_for_payment", grant_with_competing_claim)
    winner = gift_claims.claim_gift_token(
        gift_token=token,
        recipient_user_id=winner_id,
        platform="telegram",
    )

    assert winner.ok is True
    assert nested["loser"].ok is False
    assert nested["loser"].status == "claim_in_progress"
    assert get_wallet(winner_id).available_tokens == 7
    assert get_wallet(loser_id).available_tokens == 0
    with db() as conn:
        row = conn.execute(
            "SELECT status,recipient_user_id FROM gift_claims WHERE gift_token=?",
            (token,),
        ).fetchone()
    assert row["status"] == "claimed"
    assert int(row["recipient_user_id"]) == winner_id


def test_failed_gift_grant_is_resumable_only_by_same_recipient(monkeypatch) -> None:
    buyer_id = 793011
    recipient_id = 793012
    other_id = 793013
    token = _paid_gift(buyer_id=buyer_id)
    original_grant = gift_claims.grant_tokens_for_payment

    def fail_grant(**_kwargs):
        raise RuntimeError("synthetic grant interruption")

    monkeypatch.setattr(gift_claims, "grant_tokens_for_payment", fail_grant)
    failed = gift_claims.claim_gift_token(
        gift_token=token,
        recipient_user_id=recipient_id,
        platform="vk",
    )
    blocked = gift_claims.claim_gift_token(
        gift_token=token,
        recipient_user_id=other_id,
        platform="max",
    )

    assert failed.ok is False
    assert failed.status == "grant_failed"
    assert blocked.ok is False
    assert blocked.status == "claim_in_progress"
    assert get_wallet(recipient_id).available_tokens == 0
    assert get_wallet(other_id).available_tokens == 0

    monkeypatch.setattr(gift_claims, "grant_tokens_for_payment", original_grant)
    recovered = gift_claims.claim_gift_token(
        gift_token=token,
        recipient_user_id=recipient_id,
        platform="vk",
    )

    assert recovered.ok is True
    assert recovered.status in {"claimed", "already_granted"}
    assert get_wallet(recipient_id).available_tokens == 7
    assert get_wallet(other_id).available_tokens == 0
