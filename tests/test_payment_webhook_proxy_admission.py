from types import SimpleNamespace

from runtime import payment_webhook_admission as admission


def _request(*, remote: str, forwarded: str = "", real_ip: str = ""):
    headers = {}
    if forwarded:
        headers["X-Forwarded-For"] = forwarded
    if real_ip:
        headers["X-Real-IP"] = real_ip
    return SimpleNamespace(remote=remote, headers=headers)


def test_loopback_proxy_uses_forwarded_client_without_global_trust(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("PAYMENT_WEBHOOK_TRUSTED_PROXY_CIDRS", raising=False)

    request = _request(remote="127.0.0.1", forwarded="203.0.113.10, 127.0.0.1")

    assert admission._client_key(request) == "client:203.0.113.10"


def test_untrusted_public_peer_cannot_spoof_forwarded_client(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("PAYMENT_WEBHOOK_TRUSTED_PROXY_CIDRS", raising=False)

    request = _request(remote="198.51.100.20", forwarded="203.0.113.10")

    assert admission._client_key(request) == "peer:198.51.100.20"


def test_explicit_proxy_cidr_enables_forwarded_client(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setenv("PAYMENT_WEBHOOK_TRUSTED_PROXY_CIDRS", "10.20.0.0/16,2001:db8:1::/64")

    ipv4 = _request(remote="10.20.5.4", forwarded="203.0.113.11")
    ipv6 = _request(remote="2001:db8:1::5", real_ip="2001:db8:2::8")

    assert admission._client_key(ipv4) == "client:203.0.113.11"
    assert admission._client_key(ipv6) == "client:2001:db8:2::8"


def test_invalid_forwarded_value_falls_back_to_direct_peer(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)

    request = _request(remote="127.0.0.1", forwarded="not-an-ip")

    assert admission._client_key(request) == "peer:127.0.0.1"


def test_two_clients_behind_loopback_proxy_have_independent_rate_windows(monkeypatch):
    admission.reset_payment_webhook_admission_state_for_tests()
    monkeypatch.setenv("PAYMENT_WEBHOOK_RATE_LIMIT", "1")
    monkeypatch.setenv("PAYMENT_WEBHOOK_RATE_WINDOW_SEC", "60")

    first = admission._client_key(_request(remote="127.0.0.1", forwarded="203.0.113.21"))
    second = admission._client_key(_request(remote="127.0.0.1", forwarded="203.0.113.22"))

    assert admission._rate_allowed(first, now=100.0) is True
    assert admission._rate_allowed(first, now=101.0) is False
    assert admission._rate_allowed(second, now=101.0) is True
