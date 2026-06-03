# P0/P1 Remediation Status

This document is intentionally code-adjacent: it records which audit findings were
closed by repository changes and which findings remain large refactors that must
not be faked with cosmetic patches.

## Closed by code

### Payment amount unit contract

- Added `services/payments/amounts.py` as the canonical conversion layer.
- Runtime contract: `plans.price` is rubles.
- Legacy kopeck rows remain handled by the one-time `price_rub_migration_v1` migration only.
- Runtime code must not use heuristics such as `price >= 50000 and price % 100 == 0` because that can corrupt legitimate high-ticket prices.

### Plan runtime price parsing

- Removed the runtime kopeck/ruble guessing heuristic from `services/plans.py`.
- `get_plan_by_id()` and related reads now return the stored ruble value directly.

### Production guardrail validator

- Added `services/validators/prod.py`.
- Production now has a validator-level contract for requiring:
  - `VALIDATOR_RELEASE_MODE=1`
  - `VALIDATOR_GUARDRAILS_STRICT=1`
- Emergency bypass exists only through explicit `ALLOW_UNGUARDED_PROD=1`.

### Payment processing state schema

- Added payment processing state columns in the canonical schema part:
  - `processing_status`
  - `granted_at_utc`
  - `side_effects_done_at_utc`
  - `notified_at_utc`
  - `processing_error`
- Added `idx_payments_processing_status`.

These fields are the base for a resumable payment processor/outbox, so duplicate
payment updates and crashes can be recovered without replaying grants incorrectly.

## Not safely closed in this patch set

### Full single-writer conversion

The repository still contains many direct `db()/conn.execute()` write paths. A
real single-writer conversion requires a domain-by-domain migration and regression
suite. A mass replacement would be unsafe and could break payments, scheduler jobs,
admin tariffs, or mood/session writes.

Required next stage:

1. Classify every DB write by domain.
2. Move non-transactional writes to canonical service/effect functions.
3. Keep only bounded transaction owners in explicit allow-lists.
4. Add a validator that fails new direct writes outside those owners.

### Job-based auto-audio delivery

The current auto-audio tick still performs subscriber scanning. Replacing it with
pure due-job scheduling is the right architecture, but it touches user time setting,
subscription grant, timezone policy, quiet-hours policy, and delivery retry logic.
It must be implemented with fixtures before enabling in production.

Required next stage:

1. Add `auto_audio_pre_score` job type.
2. Schedule per-user next slot on time change and subscription activation.
3. After successful prompt, schedule the next eligible slot.
4. Keep idempotency per `(user_id, local_day, slot, stage)`.
5. Disable the O(N) scan only after parity tests pass.

### Resumable payment processor integration

The schema is ready, but `successful_payment()` still needs to delegate all grant,
funnel cancellation, referral reward, notification, and reminder scheduling to a
single idempotent processor. This should be done in a focused payment patch, with
fixtures for duplicate Telegram updates and crash-after-grant recovery.
