from __future__ import annotations

from runtime.payment_http import _normalize_payment_kind, _package_error_response


def test_payment_kind_normalization_promotes_package_links_to_tokens():
    assert _normalize_payment_kind('subscription', 'practice_20') == 'tokens'
    assert _normalize_payment_kind('unknown', 'practice_20') == 'tokens'
    assert _normalize_payment_kind('tokens', 'practice_20') == 'tokens'
    assert _normalize_payment_kind('subscription', '') == 'subscription'
    assert _normalize_payment_kind('gift', 'practice_20') == 'gift'


def test_unknown_practice_package_returns_bad_request():
    response = _package_error_response('practice_unknown')
    assert response is not None
    assert response.status == 400
    assert 'Неизвестный пакет практик' in response.text
    assert _package_error_response('practice_20') is None
