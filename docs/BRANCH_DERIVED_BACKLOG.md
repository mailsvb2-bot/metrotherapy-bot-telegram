# Branch-derived architecture backlog

This document preserves useful ideas from old donor branches without merging stale runtime rewrites into `main`.

## P1 — safe proof and diagnostics

### Live payment closure probe

Goal: provide an explicit, manual proof that public checkout and local YooKassa webhook reconciliation work end-to-end.

Required safety properties:

- must require `--allow-live-db-mutation` before writing to the configured app DB;
- must write synthetic payment ids with a visible prefix;
- must emit a JSON report;
- must not contain production secrets;
- must not default to mutating production state silently.

### Live duplicate-webhook idempotency probe

Goal: prove that duplicate successful YooKassa webhooks do not double-grant wallet balance, payment rows, token grants, ledger rows, premium entitlements, delivery outbox rows or consultation requests.

Current main already has unit coverage in `tests/test_yookassa_webhook_idempotency.py`. A live probe may be added later with the same explicit mutation guard.

### DB stress diagnostic

Goal: provide a manual SQLite concurrency diagnostic that does not touch production tables by default.

Current main has `scripts/stress_db.py`, which uses a temp SQLite database unless `--db-path` is explicitly supplied.

## P2 — unified messenger contracts

Donor branches contained a larger `interfaces/messaging` tree with Telegram/VK/MAX contracts, renderers, adapters and bridge tests.

Do not import that tree directly into main now. It risks creating a second messaging brain next to the current runtime surfaces:

- `runtime/messenger_webhooks.py`;
- `services/messenger/*`;
- `services.messenger.outbound.SenderRegistry`;
- current Telegram polling runtime.

Future clean branch name:

```text
p2/unified-messenger-contracts
```

Acceptance criteria for that future branch:

- one canonical message contract;
- no duplicate routing/decision layer;
- current Telegram polling and VK/MAX webhook behavior preserved;
- parity tests for Telegram/VK/MAX buttons, score payloads and audio delivery;
- admin/control-plane visibility for messenger preflight and delivery errors.

## P2 — MAX webhook registration tool

Donor branches contained a `register_max_webhook.py` tool. Preserve the idea, but do not add an auto-mutating production tool without guardrails.

Future implementation requirements:

- explicit `--apply` flag;
- dry-run default;
- no secrets in stdout;
- clear JSON report;
- validates `MAX_BOT_TOKEN`, public webhook URL and expected endpoint before registering;
- never runs from startup automatically.

## P2 — split messenger webhook runtime

Donor branches contained an old split of `runtime/messenger_webhooks.py` into payload/UI modules.

The idea is valid, but direct import is risky. Future refactor should be behavior-preserving and test-first.

Suggested clean targets:

- `runtime/messenger_payloads.py` for parsing/normalization only;
- `runtime/messenger_vk_ui.py` for VK keyboard rendering only;
- `runtime/messenger_max_ui.py` for MAX keyboard rendering only;
- `runtime/messenger_webhooks.py` remains the single ingress owner.

Hard rule: no new ingress owner and no second webhook router.

## P2 — admin-controlled package catalog

Donor branches contained a DB-backed practice package catalog. The idea is useful, but it must not replace `services.practice_token_contract` until admin controls exist.

Future requirements:

- admin package editor;
- audit trail for price/package changes;
- rollback of package configuration;
- read-only public catalog projection;
- tests proving legacy package ids remain accepted for existing payment links;
- no second payment intent source.

Until then, `services.practice_token_contract` remains the source of truth for public package ids and prices.

## P3 — explicitly rejected donor content

Do not re-import:

- old `runtime/messenger_webhooks.py` rewrites;
- old `runtime/messenger_senders.py` rewrites;
- patch scripts that mutate runtime files;
- old DB-backed pricing as the active source of truth;
- old funnel runtime changes that create another decision branch;
- deployment helper scripts that embed machine-specific assumptions.
