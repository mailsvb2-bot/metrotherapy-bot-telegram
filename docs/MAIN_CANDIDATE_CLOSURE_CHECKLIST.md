# Main candidate closure checklist

This checklist must be completed before `integration/main-candidate-v1` is merged into `main`.

Last confirmed server proof:

- branch: `integration/main-candidate-v1`
- relation to `main`: ahead by 75 commits, behind by 0 commits
- full pytest: `234 passed`
- `scripts/production_acceptance.py`: OK
- `scripts/runtime_observability_check.py`: OK after service restart
- public `/pay/yookassa` route reaches backend and returns the current premium package-ladder copy
- public checkout redirect proof: all four public packages return `302` to YooMoney/YooKassa
- synthetic YooKassa webhook proof: `practice_60`, `practice_antistress_60` and `practice_personal_month` grant expected live DB rows
- duplicate YooKassa webhook proof: repeated webhooks return `inserted=false` and produce zero second deltas for wallet, payment, grant, ledger, entitlement, outbox and consultation request rows
- `git status --short`: clean after final deploy check

## P0 — repository proof

- [x] Branch is based on the latest green `feature/practice-token-economy-v2` commit.
- [x] `python -m compileall -q app.py main.py config core handlers interfaces keyboards runtime scripts services tests tools` passes through `scripts/production_acceptance.py`.
- [x] `python -m pytest -q` passes on the target server: `234 passed`.
- [x] `python scripts/production_acceptance.py` passes on the target server.
- [x] `python scripts/runtime_observability_check.py` passes after service restart.
- [x] `integration/main-candidate-v1` is not behind `main`.
- [x] `git status` is clean on the server after final deploy check.

## P0 — payment and package proof

- [x] Public `/pay/yookassa` reaches Python backend through nginx.
- [x] Missing `package_id` returns the current package-ladder copy, not legacy `5/20/60` copy.
- [x] `practice_start_7` checkout redirects to YooKassa/YooMoney.
- [x] `practice_60` checkout redirects to YooKassa/YooMoney.
- [x] `practice_antistress_60` checkout redirects to YooKassa/YooMoney.
- [x] `practice_personal_month` checkout redirects to YooKassa/YooMoney.
- [x] YooKassa webhook secret is set and accepted by the local reconciliation endpoint.
- [x] `practice_60` successful webhook grants 60 practices.
- [x] `practice_antistress_60` successful webhook grants 60 practices and video entitlement.
- [x] `practice_antistress_60` successful webhook creates/flushes video-course delivery outbox.
- [x] `practice_personal_month` successful webhook grants 60 practices, video entitlement, consultation entitlement and admin-visible consultation request.
- [x] Duplicate webhook does not double-grant practices, payments, grants, ledger rows, premium entitlements, delivery outbox rows or consultation requests in the live database.

Recommended closure command for checkout redirects:

```bash
python scripts/live_checkout_redirect_probe.py --package all --user-id 201126430 --source telegram
```

Recommended closure command for synthetic live webhook/database proof:

```bash
python scripts/live_payment_closure_probe.py \
  --apply-webhooks \
  --allow-live-db-mutation \
  --package practice_60 \
  --package practice_antistress_60 \
  --package practice_personal_month \
  --user-id 201126430 \
  --source telegram
```

Recommended closure command for duplicate-webhook idempotency proof:

```bash
python scripts/live_payment_idempotency_probe.py \
  --allow-live-db-mutation \
  --package practice_60 \
  --package practice_antistress_60 \
  --package practice_personal_month \
  --user-id 201126430 \
  --source telegram
```

The webhook/idempotency commands intentionally require `--allow-live-db-mutation` because they write synthetic payment/grant/entitlement rows into the configured application database.

## P0 — messenger proof

- [x] Telegram polling runtime is active and not conflicting with another bot instance according to observability check.
- [x] Messenger webhook runtime is active according to observability check.
- [ ] Telegram package buttons open public YooKassa links in a live Telegram chat.
- [ ] VK package links open public YooKassa links in a live VK conversation.
- [ ] MAX package links open public YooKassa links in a live MAX conversation.
- [x] Premium video message delivery is attempted via existing `SenderRegistry` in the synthetic live payment flow.
- [x] Premium entitlement remains recorded independently of delivery outbox status.

## P0 — admin/control-plane proof

- [ ] Admin payment report shows provider payment problems.
- [x] Live DB contains consultation request for `practice_personal_month` with `status=new`.
- [ ] Admin UI/report can identify `user_id`, platform, external user id, package id and payment id for each consultation request.

## P1 — salvage candidates after main candidate stabilizes

- [x] Review `feature/practice-token-economy` for additive ledger improvements:
  - `refunded_tokens`;
  - reservation expiry;
  - ledger metadata json;
  - reserved-after snapshots.
- [x] Review `fix/p1-vk-buttons-contract` for VK payload/button regression tests.
- [x] Review `canon/trial-funnel-outcome-guard-v2` for outcome guard policy and stress scripts.
- [x] Review `feature/max-messenger-canonical` for safe preflight ideas only, not whole-runtime merge.
- [x] Review `fix/vk-score-surface-20260506-221916` for score surface tests only, not whole-runtime merge.
- [x] Review `pricing/practices` and reject direct import because it would reintroduce old package ids/prices and second payment surfaces.
- [x] Review `refactor/split-messenger-webhooks` and reject direct import because current branch already has stronger direct contract tests.

## Explicit non-goals before main merge

- [x] Do not merge old token branches wholesale.
- [x] Do not replace current messenger runtime with old MAX/VK runtime rewrite.
- [x] Do not reintroduce legacy public prices as primary package copy.
- [x] Do not make DB-driven pricing the source of truth until there is an admin package-control surface and tests.
- [x] Do not claim full production-grade while SQLite is the production persistence path.

## Remaining merge blockers

The codebase is green enough to be a main candidate, but these live-flow checks remain open before calling it fully closed:

1. Real Telegram package-button flow.
2. Real VK package-link flow.
3. Real MAX package-link flow.
4. Admin UI/report proof for consultation requests and payment problems.