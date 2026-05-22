from __future__ import annotations

from services.payments.reconciliation import _practice_package_payment_problem


def test_practice_package_webhook_amount_must_match_contract():
    assert _practice_package_payment_problem(
        package_id='practice_20',
        amount_minor=349000,
        currency='RUB',
    ) == ''
    assert _practice_package_payment_problem(
        package_id='practice_20',
        amount_minor=99000,
        currency='RUB',
    ) == 'amount_mismatch_for_practice_grant'


def test_practice_package_webhook_currency_must_be_rub():
    assert _practice_package_payment_problem(
        package_id='practice_20',
        amount_minor=349000,
        currency='USD',
    ) == 'currency_mismatch_for_practice_grant'


def test_practice_package_webhook_unknown_package_is_problem():
    assert _practice_package_payment_problem(
        package_id='practice_unknown',
        amount_minor=349000,
        currency='RUB',
    ) == 'unknown_package_id_for_practice_grant'
