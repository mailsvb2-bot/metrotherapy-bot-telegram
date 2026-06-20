# Production hardening environment checklist

Set these variables before enabling the hardened payment ingress in production.

## Checkout links

```bash
PAYMENT_CHECKOUT_SIGNING_KEY=<operator-managed-random-value>
```

`/pay/yookassa` requires a signed `intent` in production by default. The temporary rollback flag is:

```bash
ALLOW_UNSIGNED_PAYMENT_CHECKOUT_IN_PROD=1
```

Use this rollback flag only during emergency migration; remove it after old unsigned links have expired.

## YooKassa webhook verification

```bash
YOOKASSA_SHOP_ID=<shop-id>
YOOKASSA_SECRET_KEY=<api-key>
YOOKASSA_WEBHOOK_SECRET=<shared-header-value>
```

The HTTP webhook verifies grant-producing `payment.succeeded` events against YooKassa before applying token/premium side effects. The temporary rollback flag is:

```bash
ALLOW_UNVERIFIED_YOOKASSA_WEBHOOK_IN_PROD=1
```

## Telegram webhook route

The canonical Telegram webhook route is tokenless and authenticated by Telegram's secret-token header.

The legacy token route is disabled by default. Enable it only as a temporary bridge:

```bash
TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED=1
```

## Final gate

After deploy, run:

```bash
python scripts/production_gate.py
```
