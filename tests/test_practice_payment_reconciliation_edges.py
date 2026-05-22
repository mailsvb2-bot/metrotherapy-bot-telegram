from __future__ import annotations

from services.db import db
from services.payments.reconciliation import _amount_to_minor_units, _practice_package_payment_problem, record_yookassa_webhook


def test_amount_to_minor_units_uses_decimal_rounding():
    assert _amount_to_minor_units({'value': '3490.00'}) == 349000
    assert _amount_to_minor_units({'value': '3490,00'}) == 349000
    assert _amount_to_minor_units({'value': '1.005'}) == 101


def test_practice_package_webhook_amount_must_match_contract():
    assert _practice_package_payment_problem(
        package_id='practice_personal_month',
        amount_minor=2300000,
        currency='RUB',
    ) == ''
    assert _practice_package_payment_problem(
        package_id='practice_personal_month',
        amount_minor=1290000,
        currency='RUB',
    ) == 'amount_mismatch_for_practice_grant'


def test_legacy_practice_package_webhook_amount_remains_supported():
    assert _practice_package_payment_problem(
        package_id='practice_20',
        amount_minor=349000,
        currency='RUB',
    ) == ''


def test_practice_package_webhook_currency_must_be_rub():
    assert _practice_package_payment_problem(
        package_id='practice_personal_month',
        amount_minor=2300000,
        currency='USD',
    ) == 'currency_mismatch_for_practice_grant'


def test_practice_package_webhook_unknown_package_is_problem():
    assert _practice_package_payment_problem(
        package_id='practice_unknown',
        amount_minor=349000,
        currency='RUB',
    ) == 'unknown_package_id_for_practice_grant'


def test_successful_repeat_webhook_replaces_stale_problem(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'reconciliation.db'))
    payment_id = 'pay-stale-problem-1'
    bad_payload = {
        'event': 'payment.succeeded',
        'object': {
            'id': payment_id,
            'status': 'succeeded',
            'amount': {'value': '12900.00', 'currency': 'RUB'},
            'metadata': {
                'user_id': '707',
                'kind': 'tokens',
                'package_id': 'practice_personal_month',
            },
        },
    }
    good_payload = {
        'event': 'payment.succeeded',
        'object': {
            'id': payment_id,
            'status': 'succeeded',
            'amount': {'value': '23000.00', 'currency': 'RUB'},
            'metadata': {
                'user_id': '707',
                'kind': 'tokens',
                'package_id': 'practice_personal_month',
            },
        },
    }

    first = record_yookassa_webhook(bad_payload)
    assert first.problem == 'amount_mismatch_for_practice_grant'
    assert first.inserted is True

    second = record_yookassa_webhook(good_payload)
    assert second.problem == ''
    assert second.inserted is False

    with db() as conn:
        payment = conn.execute(
            'SELECT problem FROM payments WHERE provider_charge_id=?',
            (payment_id,),
        ).fetchone()
        wallet = conn.execute(
            'SELECT available_tokens FROM practice_wallets WHERE user_id=?',
            (707,),
        ).fetchone()

    assert payment['problem'] == ''
    assert wallet['available_tokens'] == 60
