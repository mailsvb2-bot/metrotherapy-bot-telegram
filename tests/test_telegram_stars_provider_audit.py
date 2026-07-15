from __future__ import annotations

from pathlib import Path

from scripts import telegram_stars_provider_audit as audit


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "run_deploy_worker.sh"


def test_provider_audit_uses_native_xtr_without_provider_token(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:test-token")
    calls: list[tuple[str, dict | None]] = []

    def fake_api_call(_token: str, method: str, payload: dict | None = None):
        calls.append((method, payload))
        if method == "getMe":
            return {"ok": True, "result": {"username": "MetrotherapyBot"}}
        return {"ok": True, "result": "https://t.me/$audit-link"}

    monkeypatch.setattr(audit, "_api_call", fake_api_call)
    message, code = audit.run()

    assert code == 0
    assert message == "status=ok stage=createInvoiceLink bot=@MetrotherapyBot code=200 error=NONE"
    assert calls[1][0] == "createInvoiceLink"
    payload = calls[1][1]
    assert payload is not None
    assert payload["currency"] == "XTR"
    assert payload["prices"] == [{"label": "Audit", "amount": 1}]
    assert "provider_token" not in payload


def test_provider_audit_reports_provider_account_invalid_without_secret(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:secret-token")

    def fake_api_call(_token: str, method: str, payload: dict | None = None):
        del payload
        if method == "getMe":
            return {"ok": True, "result": {"username": "MetrotherapyBot"}}
        return {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: PROVIDER_ACCOUNT_INVALID",
        }

    monkeypatch.setattr(audit, "_api_call", fake_api_call)
    message, code = audit.run()

    assert code == 4
    assert "bot=@MetrotherapyBot" in message
    assert "error=PROVIDER_ACCOUNT_INVALID" in message
    assert "secret-token" not in message


def test_deploy_worker_publishes_sanitized_provider_audit_result() -> None:
    source = WORKER.read_text(encoding="utf-8")

    assert "[stars-provider-audit-request]" in source
    assert "[stars-provider-audit-result]" in source
    assert "telegram_stars_provider_audit.py" in source
    assert "cut -c1-180" in source
    assert source.index('/usr/bin/bash "$DEPLOY_SH"') < source.index("publish_stars_provider_audit_if_requested\n")
