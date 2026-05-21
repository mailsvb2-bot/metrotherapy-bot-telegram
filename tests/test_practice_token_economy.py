from __future__ import annotations

from services.practice_token_contract import daily_practice_cost, normalize_delivery_mode, package_by_id
from services.practice_tokens import grant_tokens, grant_tokens_for_payment, get_wallet, payment_url, render_packages_text, set_delivery_mode, get_delivery_mode


def test_practice_package_contract_prices():
    assert package_by_id('practice_5').tokens == 5
    assert package_by_id('practice_20').price_rub == 3490
    assert package_by_id('practice_60').title == '60 практик'


def test_delivery_mode_normalization_and_cost():
    assert normalize_delivery_mode('утро') == 'morning_only'
    assert normalize_delivery_mode('вечер') == 'evening_only'
    assert normalize_delivery_mode('утро + вечер') == 'both'
    assert daily_practice_cost('both') == 2
    assert daily_practice_cost('paused') == 0
    assert daily_practice_cost('morning_only') == 1


def test_grant_tokens_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'tokens.db'))

    inserted, wallet, ledger_id = grant_tokens(
        101,
        package_id='practice_5',
        amount=5,
        provider='test',
        provider_payment_id='p1',
        idempotency_key='grant:test:p1',
    )
    assert inserted is True
    assert ledger_id is not None
    assert wallet.available_tokens == 5

    inserted_again, wallet_again, _ = grant_tokens(
        101,
        package_id='practice_5',
        amount=5,
        provider='test',
        provider_payment_id='p1',
        idempotency_key='grant:test:p1',
    )
    assert inserted_again is False
    assert wallet_again.available_tokens == 5


def test_payment_grant_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'payments.db'))

    inserted, wallet, _ = grant_tokens_for_payment(
        provider='yookassa',
        provider_payment_id='pay-1',
        user_id=202,
        package_id='practice_20',
    )
    assert inserted is True
    assert wallet.available_tokens == 20

    inserted_again, wallet_again, _ = grant_tokens_for_payment(
        provider='yookassa',
        provider_payment_id='pay-1',
        user_id=202,
        package_id='practice_20',
    )
    assert inserted_again is False
    assert wallet_again.available_tokens == 20


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
    assert '5 практик — 990 ₽' in text
    assert '20 практик — 3 490 ₽' in text
    assert '60 практик — 7 900 ₽' in text
    assert 'kind=tokens' in text
    assert 'package_id=practice_20' in text


def test_payment_url_uses_external_user_id():
    url = payment_url(
        'https://bot.example',
        user_id=1,
        platform='vk',
        external_user_id='777',
        package_id='practice_60',
    )
    assert url == 'https://bot.example/pay/yookassa?source=vk&user_id=777&kind=tokens&package_id=practice_60'
