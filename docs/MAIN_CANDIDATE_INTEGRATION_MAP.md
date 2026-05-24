# Main candidate integration map

This branch is the staging area for turning the current green branch into the future `main`.

Base branch:

- `feature/practice-token-economy-v2`
- base commit: `57b6a30 Align payment package error copy with premium ladder`

Current known green proof from server:

- full pytest: `229 passed`
- `scripts/production_acceptance.py`: OK
- `scripts/runtime_observability_check.py`: OK
- public `/pay/yookassa` route reaches backend and uses the current premium package ladder copy

## Canonical target

`integration/main-candidate-v1` must preserve these live features:

1. Telegram polling runtime.
2. VK/MAX webhook runtime.
3. YooKassa public checkout route.
4. Practice token economy.
5. Premium package ladder:
   - `practice_start_7` — 1,900 RUB, 7 practices;
   - `practice_60` — 7,900 RUB, 60 practices;
   - `practice_antistress_60` — 12,900 RUB, 60 practices plus stress video course;
   - `practice_personal_month` — 23,000 RUB, 60 practices plus stress video course plus one 60-minute consultation request.
6. Premium entitlements and delivery outbox.
7. Consultation request admin visibility.
8. Runtime health/readiness/observability checks.

## Branch salvage decisions

| Source branch | Status | Decision |
| --- | --- | --- |
| `feature/practice-token-economy-v2` | canonical green base | Keep as source of truth until main PR is opened. |
| `feature/practice-token-economy` | diverged; old token economy | Do not merge. Salvaged in this branch: `refunded_tokens`, reservation expiry, richer ledger metadata and reservation audit fields. Do not import old public pricing/UI. |
| `pricing/practices` | audited | Do not merge. It contains old DB-backed package catalog and old `practice_5/practice_20/practice_60` pricing. Keep only the future idea of admin-controlled package catalog, after an admin/control-plane surface exists. |
| `canon/trial-funnel-outcome-guard-v2` | audited | Do not merge. Safe pieces are already present or salvaged: isolated DB stress probe, safe ingress stress probe, pure trial funnel policy with tests. Do not import old production acceptance script or old funnel runtime changes. |
| `feature/max-messenger-canonical` | large diverged MAX/VK/Telegram rewrite | Do not merge. Salvage tests and interface ideas only after current runtime parity is locked. |
| `fix/vk-score-surface-20260506-221916` | large diverged VK/MAX score surface | Do not merge. Salvage edge-case tests only. |
| `refactor/split-messenger-webhooks` | diverged refactor | Do not merge. Use as blueprint for future split, not as code source. |
| `fix/p1-vk-buttons-contract` | audited | No merge needed. Current branch already contains the useful VK keyboard/payload parity tests and stronger MAX score payload coverage. |
| branches with `ahead_by=0` versus `feature/practice-token-economy-v2` | already absorbed/behind | Keep as archival until `main` cut is complete, then delete after confirmation. |

## Completed integration waves

### Wave 1 — integration contract

- Added `docs/MAIN_CANDIDATE_INTEGRATION_MAP.md`.
- Added `docs/MAIN_CANDIDATE_CLOSURE_CHECKLIST.md`.

### Wave 2 — token ledger audit salvage

Source: `feature/practice-token-economy`.

Integrated safely:

- additive migration `practice_token_audit_v2`;
- `practice_wallets.refunded_tokens`;
- `practice_ledger.reserved_after`;
- `practice_ledger.metadata_json`;
- `practice_ledger.session_id`;
- `practice_ledger.audio_anchor`;
- `practice_ledger.reservation_id`;
- `practice_reservations.expires_at`;
- regression test for grant/reserve/consume audit context.

Explicitly not imported:

- old public package prices;
- old DB-backed package catalog as source of truth;
- old UI/payment copy.

### Wave 3 — VK buttons branch audit

Source: `fix/p1-vk-buttons-contract`.

Decision: no code import.

Reason:

- current `runtime/messenger_payloads.py` already preserves VK payload extraction, menu command normalization, nested payload extraction and MAX native score payload behavior;
- current `tests/test_messenger_webhook_split_parity.py` already includes the donor branch keyboard/payload parity checks;
- current branch also has stronger MAX score button coverage, so importing the old runtime payload file would be a regression risk.

### Wave 4 — safe stress probe salvage

Source: `canon/trial-funnel-outcome-guard-v2`.

Integrated safely:

- `scripts/stress_db.py`: isolated database stress probe that writes only into `stress_probe_events`, tags rows by `run_id`, and deletes its own rows by default;
- `scripts/stress_ingress.py`: safe ingress stress probe for health endpoints and ignored VK/MAX events.

Explicitly not imported:

- old `scripts/production_acceptance.py`, because the current main-candidate acceptance script is newer and already aligned with the premium package ladder;
- `services/funnel2.py` changes, pending separate runtime-flow review;
- any script as an automatic deploy gate. These probes are manual P1 diagnostics only.

### Wave 5 — trial funnel policy audit

Source: `canon/trial-funnel-outcome-guard-v2`.

Decision: no code import.

Reason:

- `services/trial_funnel_policy.py` is already present in this branch as a pure policy surface;
- `tests/test_trial_funnel_policy.py` already covers missing outcome, negative outcome, neutral outcome, positive outcome and non-sales steps;
- old `services/funnel2.py` runtime changes remain intentionally out of scope until a separate scheduled-funnel integration review.

### Wave 6 — pricing practices branch audit

Source: `pricing/practices`.

Decision: no code import.

Reason:

- donor branch is 67 commits behind this main-candidate;
- donor branch introduces a DB-backed `practice_packages` catalog, but with old package ids and prices (`practice_5`, `practice_20`, `practice_60`);
- donor branch introduces separate `payment_intents` / `payment_practice_grants` surfaces while this branch already has canonical YooKassa reconciliation, token grants, premium entitlements and delivery outbox;
- importing this branch would risk a second source of truth for package pricing.

Future salvage idea:

- DB-backed package catalog may become useful only after there is an admin/control-plane package editor and tests that preserve the current premium ladder.

## Integration order

### P0 — main candidate proof

1. Keep `integration/main-candidate-v1` green against current tests.
2. Add integration map and closure checklist.
3. Do not import old runtime rewrites before main-readiness proof.

### P1 — safe salvage

1. Add missing tests that do not require old runtime architecture.
2. Add ledger metadata fields only via additive migration and compatibility tests. Done in Wave 2.
3. Add stress scripts only if they compile without non-standard dependencies and do not mutate runtime state by default. Done in Wave 4.

### P2 — risky salvage

1. MAX/VK/Telegram unified interface refactor.
2. Split `runtime/messenger_webhooks.py` into smaller modules.
3. DB-backed practice package admin surface.
4. Scheduled funnel runtime integration with `trial_funnel_policy.py`.

## Main PR stop-condition

A PR from `integration/main-candidate-v1` to `main` is allowed only if all pass on a clean checkout/server:

```bash
python -m pytest -q
python scripts/production_acceptance.py
python scripts/runtime_observability_check.py
```

Manual live-flow stop-conditions:

1. Telegram demo flow.
2. VK inbound message and button flow.
3. MAX inbound message and button flow.
4. YooKassa test payment for `practice_60`.
5. YooKassa test payment for `practice_antistress_60` creates video entitlement/outbox.
6. YooKassa test payment for `practice_personal_month` creates video entitlement, consultation entitlement and admin-visible request.
