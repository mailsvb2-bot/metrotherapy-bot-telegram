from __future__ import annotations

from services.messenger.bridge import issue_bridge_token
from services.messenger.entrypoints import register_user_entry
from services.practice_tokens import (
    check_and_reserve_for_audio,
    consume_reservation,
    finalize_audio_access,
    get_delivery_mode,
    get_wallet,
    grant_tokens,
    grant_tokens_for_payment,
    payment_url,
    release_reservation,
    render_packages_text,
    render_rhythm_text,
    reserve_practice,
    set_delivery_mode,
)


def test_grant_tokens_is_idempotent():
    inserted, wallet, ledger_id = grant_tokens(
        90101,
        package_id="practice_start_7",
        amount=7,
        provider="test",
        provider_payment_id="p-lifecycle-1",
        idempotency_key="grant:test:lifecycle:p1",
    )
    assert inserted is True
    assert ledger_id is not None
    assert wallet.available_tokens == 7

    inserted_again, wallet_again, _ = grant_tokens(
        90101,
        package_id="practice_start_7",
        amount=7,
        provider="test",
        provider_payment_id="p-lifecycle-1",
        idempotency_key="grant:test:lifecycle:p1",
    )
    assert inserted_again is False
    assert wallet_again.available_tokens == 7


def test_payment_grant_is_idempotent():
    inserted, wallet, _ = grant_tokens_for_payment(
        provider="yookassa",
        provider_payment_id="pay-lifecycle-1",
        user_id=90202,
        package_id="practice_60",
    )
    assert inserted is True
    assert wallet.available_tokens == 60

    inserted_again, wallet_again, _ = grant_tokens_for_payment(
        provider="yookassa",
        provider_payment_id="pay-lifecycle-1",
        user_id=90202,
        package_id="practice_60",
    )
    assert inserted_again is False
    assert wallet_again.available_tokens == 60


def test_reserve_consume_lifecycle():
    grant_tokens(
        90505,
        package_id="practice_start_7",
        amount=2,
        idempotency_key="grant:reserve-consume-lifecycle",
    )
    ok, wallet, reservation_id = reserve_practice(90505, session_id=11, audio_anchor=7)
    assert ok is True
    assert reservation_id
    assert wallet.available_tokens == 1
    assert wallet.reserved_tokens == 1

    assert consume_reservation(str(reservation_id)) is True
    wallet_after = get_wallet(90505)
    assert wallet_after.available_tokens == 1
    assert wallet_after.reserved_tokens == 0
    assert wallet_after.used_tokens == 1

    # A duplicated success callback is an idempotent success, not an error. The
    # wallet must remain unchanged and no second consume ledger event is created.
    assert consume_reservation(str(reservation_id)) is True
    assert get_wallet(90505) == wallet_after


def test_reserve_release_lifecycle():
    grant_tokens(
        90506,
        package_id="practice_start_7",
        amount=1,
        idempotency_key="grant:reserve-release-lifecycle",
    )
    ok, wallet, reservation_id = reserve_practice(90506, session_id=12, audio_anchor=8)
    assert ok is True
    assert wallet.available_tokens == 0
    assert wallet.reserved_tokens == 1

    assert release_reservation(str(reservation_id)) is True
    wallet_after = get_wallet(90506)
    assert wallet_after.available_tokens == 1
    assert wallet_after.reserved_tokens == 0
    assert wallet_after.used_tokens == 0

    # A duplicated failure callback must be safe to acknowledge without changing
    # the refunded wallet a second time.
    assert release_reservation(str(reservation_id)) is True
    assert get_wallet(90506) == wallet_after


def test_access_guard_hard_blocks_without_balance(monkeypatch):
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")

    decision = check_and_reserve_for_audio(90606, is_demo=False, session_id=1, audio_anchor=1)
    assert decision.allowed is False
    assert decision.reason == "insufficient_balance"
    assert "нет доступных практик" in decision.message


def test_access_guard_reserves_and_finalize_releases_on_failure(monkeypatch):
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")

    grant_tokens(
        90607,
        package_id="practice_start_7",
        amount=1,
        idempotency_key="grant:guard-release-lifecycle",
    )
    decision = check_and_reserve_for_audio(90607, is_demo=False, session_id=1, audio_anchor=1)
    assert decision.allowed is True
    assert decision.reason == "reserved"
    assert decision.reservation_id
    assert get_wallet(90607).available_tokens == 0
    assert get_wallet(90607).reserved_tokens == 1

    finalize_audio_access(decision, delivered=False)
    assert get_wallet(90607).available_tokens == 1
    assert get_wallet(90607).reserved_tokens == 0


def test_access_guard_reserves_and_finalize_consumes_on_success(monkeypatch):
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")

    grant_tokens(
        90608,
        package_id="practice_start_7",
        amount=1,
        idempotency_key="grant:guard-consume-lifecycle",
    )
    decision = check_and_reserve_for_audio(90608, is_demo=False, session_id=1, audio_anchor=1)
    assert decision.allowed is True
    assert decision.reservation_id

    finalize_audio_access(decision, delivered=True)
    wallet = get_wallet(90608)
    assert wallet.available_tokens == 0
    assert wallet.reserved_tokens == 0
    assert wallet.used_tokens == 1


def test_access_guard_soft_allows_without_balance(monkeypatch):
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "soft")

    decision = check_and_reserve_for_audio(90609, is_demo=False, session_id=1, audio_anchor=1)
    assert decision.allowed is True
    assert decision.reason == "soft_insufficient_balance"
    assert "нет доступных практик" in decision.warning


def test_delivery_mode_is_saved():
    assert set_delivery_mode(90303, "both") == "both"
    assert get_delivery_mode(90303) == "both"
    assert set_delivery_mode(90303, "пауза") == "paused"
    assert get_delivery_mode(90303) == "paused"


def test_render_packages_text_contains_canonical_package_payment_links():
    text = render_packages_text(
        90404,
        base_url="https://bot.example",
        platform="telegram",
        external_user_id="404",
    )

    assert "Пакеты практик" in text
    assert "1 практика = одно аудио" in text
    assert "Сейчас у Вас:" in text
    assert "Стартовый пакет — 1 900 ₽" in text
    assert "Полный маршрут — 7 900 ₽" in text
    assert "Антистресс-система — 12 900 ₽" in text
    assert "Персональный месяц — 23 000 ₽" in text
    assert "kind=tokens" in text
    assert "package_id=practice_start_7" in text
    assert "package_id=practice_60" in text
    assert "package_id=practice_antistress_60" in text
    assert "package_id=practice_personal_month" in text
    assert "package_id=practice_5" not in text
    assert "package_id=practice_20" not in text


def test_render_rhythm_text_is_localized():
    text = render_rhythm_text(90405)

    assert "Ритм практик" in text
    assert "Только утро" in text
    assert "Только вечер" in text
    assert "Пауза" in text


def test_payment_url_uses_external_user_id():
    url = payment_url(
        "https://bot.example",
        user_id=1,
        platform="vk",
        external_user_id="777",
        package_id="practice_personal_month",
    )
    assert url == "https://bot.example/pay/yookassa?source=vk&user_id=777&kind=tokens&package_id=practice_personal_month"


def test_practice_wallet_is_account_native_across_linked_messengers():
    token = issue_bridge_token(910010, target_platform="vk")
    linked = register_user_entry(
        920020,
        platform="vk",
        external_user_id="920020",
        start_payload=f"bridge_{token}",
    )
    assert linked.user_id == 910010

    inserted, wallet, _ = grant_tokens(
        910010,
        package_id="practice_start_7",
        amount=3,
        provider="test",
        provider_payment_id="account-wallet-cross-channel",
        idempotency_key="grant:test:account-wallet-cross-channel",
    )
    assert inserted is True
    assert wallet.available_tokens == 3

    vk_wallet = get_wallet(920020)
    assert vk_wallet.user_id == 910010
    assert vk_wallet.available_tokens == 3

    ok, wallet_after_reserve, reservation_id = reserve_practice(920020, session_id=1, audio_anchor=1)
    assert ok is True
    assert reservation_id
    assert wallet_after_reserve.user_id == 910010
    assert wallet_after_reserve.available_tokens == 2
    assert wallet_after_reserve.reserved_tokens == 1

    finalize_audio_access(
        check_and_reserve_for_audio(920020, is_demo=False, session_id=2, audio_anchor=2),
        delivered=False,
    )

    wallet_after_release = get_wallet(910010)
    assert wallet_after_release.user_id == 910010
    assert wallet_after_release.available_tokens == 2
    assert wallet_after_release.reserved_tokens == 1


def test_payment_grant_to_linked_external_identity_credits_account_wallet():
    token = issue_bridge_token(910011, target_platform="max")
    linked = register_user_entry(
        930030,
        platform="max",
        external_user_id="930030",
        start_payload=f"bridge_{token}",
    )
    assert linked.user_id == 910011

    inserted, wallet, _ = grant_tokens_for_payment(
        provider="yookassa",
        provider_payment_id="pay-account-wallet-cross-channel",
        user_id=930030,
        package_id="practice_start_7",
    )

    assert inserted is True
    assert wallet.user_id == 910011
    assert wallet.available_tokens == 7
    assert get_wallet(910011).available_tokens == 7
    assert get_wallet(930030).available_tokens == 7


def test_delivery_mode_is_account_native_across_linked_messengers():
    token = issue_bridge_token(910012, target_platform="vk")
    linked = register_user_entry(
        940040,
        platform="vk",
        external_user_id="940040",
        start_payload=f"bridge_{token}",
    )
    assert linked.user_id == 910012

    assert set_delivery_mode(940040, "both") == "both"
    assert get_delivery_mode(910012) == "both"
    assert get_delivery_mode(940040) == "both"
