# P0/P1 production hardening

This change set closes the main production hardening items from the audit without changing the user-facing Metrotherapy flow.

## Implemented

- Signed public checkout intents for `/pay/yookassa`.
  - Production requires a valid `intent` by default.
  - Emergency rollback is explicit via `ALLOW_UNSIGNED_PAYMENT_CHECKOUT_IN_PROD=1`.
  - Generated Telegram and multi-messenger package links now include `intent`.

- Provider source-of-truth verification for YooKassa grant-producing webhooks.
  - The HTTP webhook path calls `record_verified_yookassa_webhook()`.
  - For `payment.succeeded` practice/package grants, production verifies the payment with YooKassa before side effects are applied.
  - Emergency rollback is explicit via `ALLOW_UNVERIFIED_YOOKASSA_WEBHOOK_IN_PROD=1`.

- Legacy Telegram token webhook route disabled by default.
  - The tokenless route remains canonical.
  - `/telegram-webhook/{BOT_TOKEN}` is registered only when `TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED=1`.

- Readiness schema contract expanded.
  - `/readyz` now checks the business/payment tables required to serve paid users, not only `users` and `jobs`.

- CI hardening.
  - Dev requirements include `bandit` and `pip-audit`.
  - CI now runs targeted mypy checks for payment hardening code, bandit over payment/runtime payment surfaces, and dependency audit.

## Required production environment additions

For strict production checkout links:

```bash
PAYMENT_CHECKOUT_SIGNING_KEY=<operator-managed-random-key>
```

For YooKassa provider verification and webhook reconciliation:

```bash
YOOKASSA_SHOP_ID=<shop-id>
YOOKASSA_SECRET_KEY=<api-key>
YOOKASSA_WEBHOOK_SECRET=<shared-webhook-header-value>
```

## Required live proof after merge

Run the non-bypassable gate on the server:

```bash
python scripts/production_gate.py
```

The project should not be called broadly production-ready until this gate is green on the real deployment with live env, Postgres target, health/readiness and restore drill.
