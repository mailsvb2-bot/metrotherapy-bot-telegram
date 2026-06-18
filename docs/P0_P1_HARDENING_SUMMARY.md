# P0/P1 hardening summary

Branch: `hardening-p0-p1-production`

## Code surfaces changed

- `runtime/payment_http.py`
- `runtime/messenger_webhooks.py`
- `runtime/health_server.py`
- `services/payments/checkout_intent.py`
- `services/payments/yookassa_provider.py`
- `services/payments/verified_reconciliation.py`
- `services/payments/ui.py`
- `services/messenger/package_payment_ui.py`
- `.github/workflows/ci.yml`
- `requirements-dev.txt`
- `pyproject.toml`

## Closed audit items

- P0: signed checkout intent for public payment links.
- P0: server-side YooKassa source-of-truth verification before grant-producing reconciliation.
- P1: legacy Telegram token webhook route disabled by default.
- P1: readiness expanded to the paid-user schema surface.
- P1: CI adds targeted type/security/dependency gates for the hardened payment path.

## Non-code production requirement

The server must define `PAYMENT_CHECKOUT_SIGNING_KEY` before strict production checkout is enabled.

The server must run `python scripts/production_gate.py` after merge and deploy.
