from __future__ import annotations

import urllib.error

import pytest

from services.messenger import provider_transport


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://provider.example", code, "error", hdrs=None, fp=None)


def test_provider_transport_does_not_retry_permanent_http_errors(monkeypatch) -> None:
    monkeypatch.setattr(provider_transport.time, "sleep", lambda _seconds: None)
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise _http_error(401)

    with pytest.raises(provider_transport.ProviderPermanentHTTPError) as exc_info:
        provider_transport._with_retries(operation, retries=5)

    assert exc_info.value.status_code == 401
    assert calls == 1


def test_provider_transport_retries_transient_http_errors(monkeypatch) -> None:
    monkeypatch.setattr(provider_transport.time, "sleep", lambda _seconds: None)
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _http_error(503)
        return {"ok": True}

    assert provider_transport._with_retries(operation, retries=3) == {"ok": True}
    assert calls == 3


def test_provider_transport_retry_override_can_disable_inner_retries(monkeypatch) -> None:
    monkeypatch.setattr(provider_transport.time, "sleep", lambda _seconds: None)
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise urllib.error.URLError("ambiguous network failure")

    with pytest.raises(urllib.error.URLError):
        provider_transport._with_retries(operation, retries=1)

    assert calls == 1
