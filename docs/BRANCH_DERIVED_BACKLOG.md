# Branch-derived architecture backlog

This document preserves useful ideas from old donor branches without merging stale runtime rewrites into `main`.

## P1 — safe proof and diagnostics

### Live payment closure probe

Implemented on current `main` through `scripts/probe_payment_reconciliation_live.py`.

The helper now:

- requires paired explicit mutation authorization before writing;
- uses visibly synthetic payment and reserved user ids;
- verifies duplicate-webhook idempotency;
- emits sanitized JSON;
- performs exact cleanup and zero-residual verification;
- defaults to a true no-DB dry-run.

### Live duplicate-webhook idempotency probe

Implemented as part of the guarded live payment closure probe. The same synthetic webhook is replayed twice and the second application must not create another wallet grant, payment row, token grant, ledger row, premium entitlement, delivery outbox row or consultation request.

### DB stress diagnostic

Implemented through `scripts/stress_db.py` with a fail-closed target contract:

- the default run creates a unique temporary SQLite database and removes the database plus WAL/SHM sidecars afterwards;
- a caller-supplied `--db-path` is rejected unless `--allow-custom-db-path` is explicit;
- an existing file additionally requires `--allow-existing-db-path`;
- a path matching `METRO_DB_PATH`, a SQLite `DATABASE_URL`, or the repository application default requires `--allow-configured-db-path` and the exact confirmation phrase exposed by the script;
- worker and iteration counts are bounded;
- only rows belonging to the synthetic run are removed from a pre-existing compatible stress table;
- a table created by the diagnostic is dropped after the run, without creating a residual `sqlite_sequence` table;
- the original SQLite journal mode is restored and zero residual run rows are verified before success;
- error reports contain class/code information rather than raw database exception text.

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

Implemented on current `main` through `scripts/register_max_webhook.py`.

Current contract:

- explicit `--apply` flag is required before provider network access or mutation;
- dry-run is the default and performs no network calls;
- bot token, webhook secret, request headers and raw provider bodies never appear in stdout;
- output is a sanitized JSON report;
- `MAX_BOT_TOKEN`, a bare HTTPS public origin, exact `/webhooks/max` endpoint, secret format and official API2 origin are validated;
- existing exact subscriptions are detected and do not trigger a duplicate POST;
- a newly created subscription is re-read and must be visible before success;
- the helper is operator-invoked only and never runs from application startup.

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
