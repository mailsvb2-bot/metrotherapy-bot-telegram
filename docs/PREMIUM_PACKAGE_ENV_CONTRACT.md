# Premium package environment contract

Premium practice packages currently supported by the payment reconciliation flow:

- `practice_antistress_60`: 60 practices plus stress video course access.
- `practice_personal_month`: 60 practices plus stress video course access plus one consultation request for admin follow-up.

## Required production variables

`STRESS_VIDEO_COURSE_URL` must point to the real video course page or private course access URL.

Example:

```env
STRESS_VIDEO_COURSE_URL=https://metrotherapy.ru/antistress-course
```

`VIDEO_COURSE_URL` is accepted as a compatibility alias, but `STRESS_VIDEO_COURSE_URL` is the canonical variable.

## Payment reconciliation

Premium entitlements are granted only after either:

- a successful, signed, source-bound and amount-verified YooKassa webhook from VK, MAX or web checkout; or
- a validated Telegram `successful_payment` in `XTR` from the native Stars invoice flow.

Both providers use the same idempotent token and premium-entitlement services.

Buyer-facing RUB package amounts are owned by `services.practice_token_contract.DEFAULT_PRACTICE_PACKAGES`.
The values below document the current defaults and are covered by a contract test so operator documentation cannot silently drift from checkout validation:

- `practice_antistress_60`: `8290.00 RUB`.
- `practice_personal_month`: `24870.00 RUB`.

Telegram Stars prices are a separate explicit buyer-facing contract and are resolved through `telegram_stars_price()`; they must not be inferred from the RUB values in this document.

## Delivery behavior

After the provider payment is accepted:

1. practice tokens are granted;
2. premium entitlements are recorded;
3. video course delivery messages are queued for known Telegram/VK/MAX identities;
4. `practice_personal_month` also creates a consultation request for admin follow-up;
5. the runtime tries to flush pending premium delivery messages through the existing `SenderRegistry`.

If delivery fails, entitlements and consultation requests remain recorded, and the outbox item keeps the failure details for later remediation.
