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

## YooKassa reconciliation

Premium entitlements are granted only after a successful and amount-verified YooKassa webhook.

Expected package amounts:

- `practice_antistress_60`: `12900.00 RUB`.
- `practice_personal_month`: `23000.00 RUB`.

## Delivery behavior

After the webhook is accepted:

1. practice tokens are granted;
2. premium entitlements are recorded;
3. video course delivery messages are queued for known Telegram/VK/MAX identities;
4. `practice_personal_month` also creates a consultation request for admin follow-up;
5. the runtime tries to flush pending premium delivery messages through the existing `SenderRegistry`.

If delivery fails, entitlements and consultation requests remain recorded, and the outbox item keeps the failure details for later remediation.
