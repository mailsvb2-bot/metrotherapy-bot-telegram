from __future__ import annotations

import importlib.util
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INNER = ROOT / "scripts" / "yookassa_refund_drill_guard_inner.py"


def _load_inner():
    spec = importlib.util.spec_from_file_location("refund_idempotence_guard", INNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_long_idempotence_key_is_reduced_to_provider_limit_stably() -> None:
    module = _load_inner()
    raw = "metrotherapy-refund-drill-payment-" + "a" * 36

    first = module._normalize_idempotence_key(raw)
    second = module._normalize_idempotence_key(raw)

    assert first == second
    assert len(first) == 64
    assert first != raw[:64]


def test_short_idempotence_key_is_preserved_exactly() -> None:
    module = _load_inner()
    raw = "550e8400-e29b-41d4-a716-446655440000"

    assert module._normalize_idempotence_key(raw) == raw


def test_only_yookassa_requests_have_idempotence_header_normalized() -> None:
    module = _load_inner()
    impl = module._load_impl()
    long_key = "metrotherapy-refund-drill-refund-" + "b" * 36
    provider = urllib.request.Request(
        "https://api.yookassa.ru/v3/refunds",
        headers={"Idempotence-Key": long_key},
    )
    external = urllib.request.Request(
        "https://example.com/refunds",
        headers={"Idempotence-Key": long_key},
    )

    module._normalize_provider_request_headers(impl, provider)
    module._normalize_provider_request_headers(impl, external)

    provider_key = provider.headers.get("Idempotence-key")
    external_key = external.headers.get("Idempotence-key")
    assert provider_key is not None
    assert len(provider_key) == 64
    assert external_key == long_key
