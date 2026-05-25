# Branch archive preservation map

This document preserves the useful content and deletion policy for the remaining non-main branches after cleanup.

Current protected branches:

- `main`
- `integration/main-candidate-v1`

Current temporary archive/donor branches:

- `feature/practice-token-economy-v2`
- `feature/practice-token-economy`
- `feature/max-messenger-canonical`
- `fix/vk-score-surface-20260506-221916`
- `canon/trial-funnel-outcome-guard-v2`
- `pricing/practices`
- `refactor/split-messenger-webhooks`

## What has already been preserved in `integration/main-candidate-v1`

### From `feature/practice-token-economy-v2`

Status: base absorbed.

Preserved:

- current premium package ladder;
- YooKassa payment route;
- practice token economy v2 behavior;
- premium entitlements;
- consultation request flow;
- production acceptance alignment.

Deletion policy:

- safe to delete after PR #12 is merged into `main` and the server is redeployed from `main` with green checks.

### From `feature/practice-token-economy`

Status: useful ledger ideas salvaged; do not merge.

Preserved:

- `practice_wallets.refunded_tokens`;
- `practice_reservations.expires_at`;
- `practice_ledger.reserved_after`;
- `practice_ledger.metadata_json`;
- `practice_ledger.session_id`;
- `practice_ledger.audio_anchor`;
- `practice_ledger.reservation_id`;
- regression coverage for grant/reserve/consume audit context.

Rejected:

- old public package prices;
- old UI copy;
- old payment path changes;
- old DB-backed practice catalog as source of truth.

Deletion policy:

- safe to delete after PR #12 is merged into `main`.

### From `canon/trial-funnel-outcome-guard-v2`

Status: safe tooling and pure policy preserved; do not merge old runtime.

Preserved:

- `scripts/stress_db.py`;
- `scripts/stress_ingress.py`;
- pure `services/trial_funnel_policy.py` surface;
- `tests/test_trial_funnel_policy.py` coverage.

Rejected:

- old `scripts/production_acceptance.py`;
- old `services/funnel2.py` runtime changes.

Deletion policy:

- safe to delete after PR #12 is merged into `main`.

### From `feature/max-messenger-canonical`

Status: partial salvage only; keep temporarily as P2 architecture archive.

Preserved now:

- current-architecture messenger preflight checks;
- `services/messenger/preflight.py`;
- `tests/test_messenger_preflight.py`;
- `config.settings.MAX_API_BASE_URL`;
- some score/parity ideas through current tests.

Still useful only as future P2 reference:

- unified messaging interface idea;
- MAX/VK/Telegram adapter contracts;
- conversation bridge tests;
- webhook registration script ideas;
- sender/interface decomposition ideas.

Rejected for current main candidate:

- old `interfaces/messaging` tree;
- old `runtime/messenger_webhooks.py` rewrite;
- old `runtime/messenger_senders.py` rewrite;
- patch scripts that mutate runtime files;
- large stale runtime import.

Deletion policy:

- keep until after PR #12 is merged;
- before deleting, create a fresh future issue or branch for `P2 unified messenger layer` if that work is still desired;
- delete only after the P2 backlog item exists or the idea is explicitly abandoned.

### From `fix/vk-score-surface-20260506-221916`

Status: score-surface test contract preserved; do not merge runtime.

Preserved:

- cross-messenger `-10..+10` score parity;
- stronger MAX score payload check: `payload.command == score:<number>`;
- VK score keyboard parity with Telegram parser and MAX keyboard.

Rejected:

- old runtime/sender rewrites;
- old `interfaces/messaging` tree;
- stale `core/engine.py`, `handlers/demo.py`, and `services/mood_text_flow.py` changes.

Deletion policy:

- safe to delete after PR #12 is merged into `main`.

### From `pricing/practices`

Status: idea only; do not merge.

Preserved as future idea:

- DB-backed package catalog;
- admin-controlled package editor concept;
- active/sort order/pricing metadata idea.

Rejected for current main candidate:

- old `practice_5`, `practice_20`, `practice_60` public pricing;
- old second payment intent/grant surfaces;
- second source of truth for package prices.

Deletion policy:

- keep until admin/report proof is closed;
- after PR #12 is merged, either create a new clean P2 branch for admin package catalog or delete this old branch.

### From `refactor/split-messenger-webhooks`

Status: blueprint only; do not merge.

Preserved/absorbed:

- direct behavior tests in `tests/test_messenger_webhook_split_parity.py`;
- current split surfaces: `runtime/messenger_payloads.py`, `runtime/messenger_vk_ui.py`, `runtime/messenger_max_ui.py`.

Still useful only as future reference:

- decomposition idea for reducing `runtime/messenger_webhooks.py` size.

Rejected:

- old legacy-vs-new parity refactor;
- heavy old `runtime/messenger_webhooks.py` rewrite.

Deletion policy:

- keep until after PR #12 is merged;
- delete after a new clean P2 refactor branch is created, if needed.

## Recommended deletion after PR #12 merge

After PR #12 is merged into `main`, the server is redeployed from `main`, and the checks below are green:

```bash
python -m pytest -q
python scripts/production_acceptance.py
systemctl restart metrotherapy.service
sleep 8
python scripts/runtime_observability_check.py
```

then these branches can be deleted safely:

```bash
git push origin --delete \
  feature/practice-token-economy-v2 \
  feature/practice-token-economy \
  fix/vk-score-surface-20260506-221916 \
  canon/trial-funnel-outcome-guard-v2
```

These branches should be deleted only after creating fresh P2 work branches/issues if the ideas remain relevant:

```bash
git push origin --delete \
  feature/max-messenger-canonical \
  pricing/practices \
  refactor/split-messenger-webhooks
```

## Current merge blockers before PR #12 is ready

The main candidate code and payment flow are strong, but these live checks remain open:

1. Real Telegram package-button flow.
2. Real VK package-link flow.
3. Real MAX package-link flow.
4. Admin UI/report proof for consultation requests and payment problems.
