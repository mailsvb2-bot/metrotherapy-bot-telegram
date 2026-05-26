# Branch salvage status

This document preserves the useful content and deletion policy for the remaining non-main branches after the branch-value salvage wave.

## Current protected branches

- `main`
- `integration/main-candidate-v1`

## Temporary archive/donor branches

- `feature/practice-token-economy-v2`
- `feature/practice-token-economy`
- `feature/max-messenger-canonical`
- `fix/vk-score-surface-20260506-221916`
- `canon/trial-funnel-outcome-guard-v2`
- `pricing/practices`
- `refactor/split-messenger-webhooks`

## Salvage decisions

| Source branch | Decision |
| --- | --- |
| `integration/main-candidate-v1` | Preserve docs, payment probes, messenger preflight, closure checklist. Do not merge wholesale. |
| `feature/practice-token-economy-v2` | Preserve package ladder tests, payment reconciliation edge tests, premium entitlement tests. Do not re-import stale runtime edits blindly. |
| `feature/practice-token-economy` | Preserve only audit ideas: refunded tokens, reservation expiry, richer ledger metadata. Do not import old public prices or old UI. |
| `canon/trial-funnel-outcome-guard-v2` | Preserve pure policy/probe ideas only. Do not import old funnel runtime changes wholesale. |
| `feature/max-messenger-canonical` | Preserve preflight/contracts as reference only. Do not import the old `interfaces/messaging` tree or runtime rewrites into main runtime. |
| `fix/vk-score-surface-20260506-221916` | Preserve VK/MAX/TG score and keyboard parity tests only. Do not import old runtime/sender rewrites. |
| `pricing/practices` | Preserve future idea of admin-controlled package catalog. Do not make DB pricing the source of truth until admin/control-plane exists. |
| `refactor/split-messenger-webhooks` | Preserve as future decomposition blueprint only. Do not replace the currently working webhook runtime. |

## Already salvaged into `main`

- Current premium package ladder.
- Practice token package contract.
- Basic practice token ledger.
- Premium entitlements and consultation request surfaces.
- YooKassa package checkout metadata.
- YooKassa reconciliation granting practice packages.
- Payment pre-checkout guard.
- Release hygiene fixes for env/db/log files.
- Validator-safe ingress stress probe.
- Store audit log outside repository checkout.
- Messenger preflight checks and tests.
- Localized practice delivery-mode aliases.

## Still worth preserving as follow-up tests/probes

These are safe to bring into `main` when adapted to the current code:

- public checkout redirect probe;
- duplicate webhook idempotency probe;
- live payment closure probe;
- admin payment report tests;
- deeper premium entitlement tests;
- payment reconciliation edge tests;
- messenger preflight tests and runtime observability checks;
- VK/MAX score and keyboard parity tests.

## Branch deletion policy

Do not delete donor branches until the adapted tests/probes above are green on `main`.

After final proof, branches with purely old runtime rewrites may be deleted only after their useful tests/docs are preserved in `main` or in a new clean P2 backlog branch.

## Remaining live-flow checks

Before claiming full production closure, verify real user-facing flows:

1. Telegram package-button flow.
2. VK package-link flow.
3. MAX package-link flow.
4. Admin report visibility for payment problems and consultation requests.
