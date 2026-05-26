# Production closure checklist

This checklist records the production-readiness evidence expected for the current `main` branch.

## P0 repository proof

- [ ] `git status --short` is clean on the server.
- [ ] `python -m pytest -q` passes.
- [ ] `APP_ENV=prod VALIDATOR_RELEASE_MODE=1 PYTHONDONTWRITEBYTECODE=1 python scripts/validate_project.py` passes.
- [ ] `APP_ENV=prod PYTHONDONTWRITEBYTECODE=1 python scripts/smoke.py` passes.
- [ ] `sudo systemctl status metrotherapy.service --no-pager -l` shows `active (running)`.
- [ ] `curl -i http://127.0.0.1:8082/healthz` returns `HTTP/1.1 200 OK` and `ok=true`.

## P0 payment/package proof

- [ ] Public `/pay/yookassa` reaches the Python backend through nginx.
- [ ] `practice_start_7` checkout returns a redirect to the payment provider.
- [ ] `practice_60` checkout returns a redirect to the payment provider.
- [ ] `practice_antistress_60` checkout returns a redirect to the payment provider.
- [ ] `practice_personal_month` checkout returns a redirect to the payment provider.
- [ ] YooKassa webhook secret is configured and accepted by the local reconciliation endpoint.
- [ ] `practice_60` successful webhook grants 60 practices.
- [ ] `practice_antistress_60` successful webhook grants 60 practices and video entitlement.
- [ ] `practice_personal_month` successful webhook grants 60 practices, video entitlement, consultation entitlement and admin-visible consultation request.
- [ ] Duplicate YooKassa webhook does not double-grant payments, wallet balance, grants, ledger rows, premium entitlements, delivery outbox rows or consultation requests.

Recommended checkout redirect proof:

```bash
python scripts/probe_checkout_redirect.py \
  --base-url https://metrotherapy-bot.metrotherapy.ru \
  --package all \
  --user-id 201126430 \
  --source telegram
```

## P0 messenger proof

- [ ] Telegram polling runtime is active and not conflicting with another bot instance.
- [ ] Messenger webhook runtime is active.
- [ ] Telegram package buttons open public YooKassa links in a live Telegram chat.
- [ ] VK package links open public YooKassa links in a live VK conversation.
- [ ] MAX package links open public YooKassa links in a live MAX conversation.
- [ ] Premium entitlement records remain stored even if delivery outbox sending fails.

## P0 admin/control-plane proof

- [ ] Admin payment report shows provider payment problems.
- [ ] Admin payment report shows `practice_personal_month` consultation requests.
- [ ] Admin report identifies `user_id`, platform, external user id, package id and payment id for each consultation request.

## Explicit non-goals for the current main branch

- Do not merge old token branches wholesale.
- Do not replace the current messenger runtime with old MAX/VK runtime rewrites.
- Do not reintroduce legacy public prices as the primary package copy.
- Do not make DB-driven pricing the source of truth until admin package-control surfaces exist.
- Do not claim full production-grade while SQLite is the production persistence path.

## Current production-grade blocker

The system can run as a hardened staging/alpha service, but it should not be called fully production-grade until persistence is migrated from SQLite to a production database profile and the live Telegram/VK/MAX package-link flows are manually proven.
