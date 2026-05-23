# Main candidate closure checklist

This checklist must be completed before `integration/main-candidate-v1` is merged into `main`.

## P0 — repository proof

- [ ] Branch is based on the latest green `feature/practice-token-economy-v2` commit.
- [ ] `python -m compileall -q app.py main.py config core handlers interfaces keyboards runtime scripts services tests tools` passes.
- [ ] `python -m pytest -q` passes on a clean checkout.
- [ ] `python scripts/production_acceptance.py` passes on the target server.
- [ ] `python scripts/runtime_observability_check.py` passes after service restart.
- [ ] `git status` is clean on the server after deploy.

## P0 — payment and package proof

- [ ] Public `/pay/yookassa` reaches Python backend through nginx.
- [ ] Missing `package_id` returns the current package-ladder copy, not legacy `5/20/60` copy.
- [ ] `practice_start_7` checkout redirects to YooKassa.
- [ ] `practice_60` checkout redirects to YooKassa.
- [ ] `practice_antistress_60` checkout redirects to YooKassa.
- [ ] `practice_personal_month` checkout redirects to YooKassa.
- [ ] YooKassa webhook secret is set in production.
- [ ] `practice_60` successful webhook grants 60 practices.
- [ ] `practice_antistress_60` successful webhook grants 60 practices and video entitlement.
- [ ] `practice_personal_month` successful webhook grants 60 practices, video entitlement, consultation entitlement and admin-visible consultation request.
- [ ] Duplicate webhook does not double-grant practices or premium entitlements.

## P0 — messenger proof

- [ ] Telegram polling runtime is active and not conflicting with another bot instance.
- [ ] Messenger webhook runtime is active.
- [ ] Telegram package buttons open public YooKassa links.
- [ ] VK package links open public YooKassa links.
- [ ] MAX package links open public YooKassa links.
- [ ] Premium video message delivery is attempted via existing `SenderRegistry`.
- [ ] Failed premium delivery remains in outbox with `last_error` and does not remove entitlement.

## P0 — admin/control-plane proof

- [ ] Admin payment report shows provider payment problems.
- [ ] Admin payment report shows consultation requests.
- [ ] Admin can identify `user_id`, platform, external user id, package id and payment id for each consultation request.

## P1 — salvage candidates after main candidate stabilizes

- [ ] Review `feature/practice-token-economy` for additive ledger improvements:
  - `refunded_tokens`;
  - reservation expiry;
  - ledger metadata json;
  - reserved-after snapshots.
- [ ] Review `fix/p1-vk-buttons-contract` for VK payload/button regression tests.
- [ ] Review `canon/trial-funnel-outcome-guard-v2` for outcome guard policy and stress scripts.
- [ ] Review `feature/max-messenger-canonical` and `fix/vk-score-surface-20260506-221916` only for tests/interface ideas, not whole-runtime merge.

## Explicit non-goals before main merge

- [ ] Do not merge old token branches wholesale.
- [ ] Do not replace current messenger runtime with old MAX/VK runtime rewrite.
- [ ] Do not reintroduce legacy public prices as primary package copy.
- [ ] Do not make DB-driven pricing the source of truth until there is an admin package-control surface and tests.
- [ ] Do not claim full production-grade while SQLite is the production persistence path.
