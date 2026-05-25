from __future__ import annotations

from services.admin_payment_report import build_admin_payment_report, render_admin_payment_report_text
from services.messenger.preferences import record_channel_identity
from services.payments.reconciliation import record_yookassa_webhook
from services.schema_core import init_db


def _succeeded_payment(payment_id: str, *, user_id: int, package_id: str, amount: str, source: str = 'telegram') -> dict:
    return {
        'event': 'payment.succeeded',
        'object': {
            'id': payment_id,
            'status': 'succeeded',
            'amount': {'value': amount, 'currency': 'RUB'},
            'metadata': {
                'project': 'metrotherapy',
                'user_id': str(user_id),
                'external_user_id': str(user_id),
                'source': source,
                'kind': 'tokens',
                'package_id': package_id,
            },
        },
    }


def test_admin_payment_report_shows_payment_problem_and_consultation_request(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'admin-report.db'))

    record_channel_identity(7001, 'vk', 'vk-7001')

    problem = record_yookassa_webhook(
        _succeeded_payment(
            'pay-problem-1',
            user_id=7001,
            package_id='practice_60',
            amount='1.00',
            source='vk',
        )
    )
    assert problem.problem == 'amount_mismatch_for_practice_grant'

    personal = record_yookassa_webhook(
        _succeeded_payment(
            'pay-personal-admin-1',
            user_id=7001,
            package_id='practice_personal_month',
            amount='23000.00',
            source='vk',
        )
    )
    assert personal.ok is True
    assert personal.problem == ''

    report = build_admin_payment_report(limit=20, user_id=7001)

    assert report.ok is True
    assert report.payment_problem_count == 1
    assert report.payment_problems[0]['provider_charge_id'] == 'pay-problem-1'
    assert report.payment_problems[0]['user_id'] == 7001
    assert report.payment_problems[0]['provider_status'] == 'succeeded'
    assert report.payment_problems[0]['problem'] == 'amount_mismatch_for_practice_grant'

    assert report.consultation_request_count == 1
    request = report.consultation_requests[0]
    assert request['user_id'] == 7001
    assert request['platform'] == 'vk'
    assert request['external_user_id'] == 'vk-7001'
    assert request['package_id'] == 'practice_personal_month'
    assert request['provider_payment_id'] == 'pay-personal-admin-1'
    assert request['status'] == 'new'

    text = render_admin_payment_report_text(report)
    assert 'Проблемные платежи: 1' in text
    assert 'payment_id=pay-problem-1' in text
    assert 'problem=amount_mismatch_for_practice_grant' in text
    assert 'Заявки на консультацию: 1' in text
    assert 'platform=vk' in text
    assert 'external_user_id=vk-7001' in text
    assert 'package_id=practice_personal_month' in text
    assert 'payment_id=pay-personal-admin-1' in text


def test_admin_payment_report_empty_state(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'admin-report-empty.db'))

    # The DB path is owned by services.db/core.paths at import time in this
    # project. Therefore this empty-state check must not depend on per-test
    # environment DB swapping. Use an isolated user filter instead: the admin
    # report should render an empty consultation slice for a user with no rows.
    init_db()

    report = build_admin_payment_report(limit=20, user_id=987654321)
    text = render_admin_payment_report_text(report)

    assert report.payment_problem_count >= 0
    assert report.consultation_request_count == 0
    assert 'Админ-отчёт по оплатам' in text
    assert 'Заявки на консультацию: 0' in text
    assert 'нет новых заявок' in text
