from __future__ import annotations

import json

from services.db import db
from runtime.payment_http import _normalize_payment_kind
from services.payments.ui import kb_practice_packages, practice_packages_text
from services.practice_token_contract import daily_practice_cost, normalize_delivery_mode, package_by_id, public_practice_packages
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
    reserve_practice,
    set_delivery_mode,
)


def test_practice_package_contract_prices():
    public_ids = [package.package_id for package in public_practice_packages()]
    assert public_ids == [
        'practice_start_7',
        'practice_60',
        'practice_antistress_60',
        'practice_personal_month',
    ]
    assert package_by_id('practice_start_7').tokens == 7
    assert package_by_id('practice_start_7').price_rub == 1900
    assert package_by_id('practice_60').price_rub == 7900
    assert package_by_id('practice_antistress_60').price_rub == 12900
    assert package_by_id('practice_personal_month').price_rub == 23000
    # Legacy package ids remain accepted for already-created payment links/webhooks,
    # but they are intentionally hidden from the public selector.
    assert package_by_id('practice_20').price_rub == 3490
    assert 'practice_20' not in public_ids


def test_delivery_mode_normalization_and_cost():
    assert normalize_delivery_mode('утро') == 'morning_only'
    assert normalize_delivery_mode('вечер') == 'evening_only'
    assert normalize_delivery_mode('утро + вечер') == 'both'
    assert daily_practice_cost('both') == 2
    assert daily_practice_cost('paused') == 0
    assert daily_practice_cost('morning_only') == 1


def test_payment_kind_normalization_promotes_package_links_to_tokens():
    assert _normalize_payment_kind('subscription', 'practice_60') == 'tokens'
    assert _normalize_payment_kind('unknown', 'practice_60') == 'tokens'
    assert _normalize_payment_kind('tokens', 'practice_60') == 'tokens'
    assert _normalize_payment_kind('subscription', '') == 'subscription'
    assert _normalize_payment_kind('gift', 'practice_60') == 'gift'


def test_grant_tokens_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'tokens.db'))

    inserted, wallet, ledger_id = grant_tokens(
        101,
        package_id='practice_start_7',
        amount=7,
        provider='test',
        provider_payment_id='p1',
        idempotency_key='grant:test:p1',
    )
    assert inserted is True
    assert ledger_id is not None
    assert wallet.available_tokens == 7
    assert wallet.refunded_tokens == 0

    inserted_again, wallet_again, _ = grant_tokens(
        101,
        package_id='practice_start_7',
        amount=7,
        provider='test',
        provider_payment_id='p1',
        idempotency_key='grant:test:p1',
    )
    assert inserted_again is False
    assert wallet_again.available_tokens == 7


def test_payment_grant_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'payments.db'))

    inserted, wallet, _ = grant_tokens_for_payment(
        provider='yookassa',
        provider_payment_id='pay-1',
        user_id=202,
        package_id='practice_60',
    )
    assert inserted is True
    assert wallet.available_tokens == 60

    inserted_again, wallet_again, _ = grant_tokens_for_payment(
        provider='yookassa',
        provider_payment_id='pay-1',
        user_id=202,
        package_id='practice_60',
    )
    assert inserted_again is False
    assert wallet_again.available_tokens == 60


def test_reserve_consume_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'reserve_consume.db'))

    grant_tokens(505, package_id='practice_start_7', amount=2, idempotency_key='grant:reserve-consume')
    ok, wallet, reservation_id = reserve_practice(505, session_id=11, audio_anchor=7)
    assert ok is True
    assert reservation_id
    assert wallet.available_tokens == 1
    assert wallet.reserved_tokens == 1

    assert consume_reservation(str(reservation_id)) is True
    wallet_after = get_wallet(505)
    assert wallet_after.available_tokens == 1
    assert wallet_after.reserved_tokens == 0
    assert wallet_after.used_tokens == 1
    assert consume_reservation(str(reservation_id)) is False


def test_reserve_release_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'reserve_release.db'))

    grant_tokens(506, package_id='practice_start_7', amount=1, idempotency_key='grant:reserve-release')
    ok, wallet, reservation_id = reserve_practice(506, session_id=12, audio_anchor=8)
    assert ok is True
    assert wallet.available_tokens == 0
    assert wallet.reserved_tokens == 1

    assert release_reservation(str(reservation_id)) is True
    wallet_after = get_wallet(506)
    assert wallet_after.available_tokens == 1
    assert wallet_after.reserved_tokens == 0
    assert wallet_after.used_tokens == 0
    assert release_reservation(str(reservation_id)) is False


def test_practice_token_audit_fields_capture_reservation_context(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'audit_fields.db'))

    grant_tokens(515, package_id='practice_start_7', amount=2, provider='test', provider_payment_id='audit-pay', idempotency_key='grant:audit')
    ok, wallet, reservation_id = reserve_practice(515, session_id=111, audio_anchor=222)
    assert ok is True
    assert wallet.available_tokens == 1
    assert reservation_id
    assert consume_reservation(str(reservation_id)) is True

    with db() as conn:
        wallet_row = conn.execute('SELECT * FROM practice_wallets WHERE user_id=?', (515,)).fetchone()
        reservation = conn.execute('SELECT * FROM practice_reservations WHERE reservation_id=?', (reservation_id,)).fetchone()
        rows = conn.execute('SELECT * FROM practice_ledger WHERE user_id=? ORDER BY id ASC', (515,)).fetchall()

    assert wallet_row['refunded_tokens'] == 0
    assert reservation['expires_at']
    assert [row['event_type'] for row in rows] == ['grant', 'reserve', 'consume']
    reserve = rows[1]
    consume = rows[2]
    assert reserve['reserved_after'] == 1
    assert reserve['session_id'] == 111
    assert reserve['audio_anchor'] == 222
    assert reserve['reservation_id'] == reservation_id
    assert json.loads(reserve['metadata_json'])['expires_at'] == reservation['expires_at']
    assert consume['reserved_after'] == 0
    assert consume['session_id'] == 111
    assert consume['audio_anchor'] == 222
    assert consume['reservation_id'] == reservation_id


def test_access_guard_hard_blocks_without_balance(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'hard_guard.db'))
    monkeypatch.setenv('TOKEN_ENFORCEMENT_MODE', 'hard')

    decision = check_and_reserve_for_audio(606, is_demo=False, session_id=1, audio_anchor=1)
    assert decision.allowed is False
    assert decision.reason == 'insufficient_balance'
    assert 'Пакеты практик' in decision.message


def test_access_guard_reserves_and_finalize_releases_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'guard_release.db'))
    monkeypatch.setenv('TOKEN_ENFORCEMENT_MODE', 'hard')

    grant_tokens(607, package_id='practice_start_7', amount=1, idempotency_key='grant:guard-release')
    decision = check_and_reserve_for_audio(607, is_demo=False, session_id=1, audio_anchor=1)
    assert decision.allowed is True
    assert decision.reason == 'reserved'
    assert decision.reservation_id
    assert get_wallet(607).available_tokens == 0
    assert get_wallet(607).reserved_tokens == 1

    finalize_audio_access(decision, delivered=False)
    assert get_wallet(607).available_tokens == 1
    assert get_wallet(607).reserved_tokens == 0


def test_access_guard_reserves_and_finalize_consumes_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'guard_consume.db'))
    monkeypatch.setenv('TOKEN_ENFORCEMENT_MODE', 'hard')

    grant_tokens(608, package_id='practice_start_7', amount=1, idempotency_key='grant:guard-consume')
    decision = check_and_reserve_for_audio(608, is_demo=False, session_id=1, audio_anchor=1)
    assert decision.allowed is True
    assert decision.reservation_id

    finalize_audio_access(decision, delivered=True)
    wallet = get_wallet(608)
    assert wallet.available_tokens == 0
    assert wallet.reserved_tokens == 0
    assert wallet.used_tokens == 1


def test_access_guard_soft_allows_without_balance(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'soft_guard.db'))
    monkeypatch.setenv('TOKEN_ENFORCEMENT_MODE', 'soft')

    decision = check_and_reserve_for_audio(609, is_demo=False, session_id=1, audio_anchor=1)
    assert decision.allowed is True
    assert decision.reason == 'soft_insufficient_balance'
    assert 'Пакеты практик' in decision.warning


def test_delivery_mode_is_saved(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'mode.db'))

    assert set_delivery_mode(303, 'both') == 'both'
    assert get_delivery_mode(303) == 'both'
    assert set_delivery_mode(303, 'пауза') == 'paused'
    assert get_delivery_mode(303) == 'paused'


def test_render_packages_text_contains_package_payment_links(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'render.db'))

    text = render_packages_text(
        404,
        base_url='https://bot.example',
        platform='telegram',
        external_user_id='404',
    )

    assert 'Ваш баланс: 0 практик' in text
    assert 'Старт без риска — 1 900 ₽' in text
    assert 'Полный маршрут — 7 900 ₽' in text
    assert 'Антистресс-система — 12 900 ₽' in text
    assert 'Личный антистресс-месяц — 23 000 ₽' in text
    assert 'kind=tokens' in text
    assert 'package_id=practice_60' in text
    assert 'package_id=practice_20' not in text


def test_telegram_practice_package_keyboard_uses_yookassa_urls(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'telegram_ui.db'))
    monkeypatch.setenv('PAYMENT_PUBLIC_BASE_URL', 'https://bot.example')

    user_id = 1707
    text = practice_packages_text(user_id)
    keyboard = kb_practice_packages(user_id, platform='telegram')
    buttons = [row[0] for row in keyboard.inline_keyboard]

    assert 'Выберите формат продолжения' in text
    assert 'Ваш баланс: 0 практик' in text
    assert [button.text for button in buttons[:4]] == [
        '🌿 Старт без риска — 1 900 ₽',
        '🌙 Полный маршрут — 7 900 ₽',
        '🎓 Антистресс-система — 12 900 ₽',
        '👤 Личный антистресс-месяц — 23 000 ₽',
    ]
    assert buttons[0].url == 'https://bot.example/pay/yookassa?source=telegram&user_id=1707&kind=tokens&package_id=practice_start_7'
    assert buttons[1].url == 'https://bot.example/pay/yookassa?source=telegram&user_id=1707&kind=tokens&package_id=practice_60'
    assert buttons[2].url == 'https://bot.example/pay/yookassa?source=telegram&user_id=1707&kind=tokens&package_id=practice_antistress_60'
    assert buttons[3].url == 'https://bot.example/pay/yookassa?source=telegram&user_id=1707&kind=tokens&package_id=practice_personal_month'
    assert buttons[4].callback_data == 'menu:main'


def test_payment_url_uses_external_user_id():
    url = payment_url(
        'https://bot.example',
        user_id=1,
        platform='vk',
        external_user_id='777',
        package_id='practice_personal_month',
    )
    assert url == 'https://bot.example/pay/yookassa?source=vk&user_id=777&kind=tokens&package_id=practice_personal_month'
