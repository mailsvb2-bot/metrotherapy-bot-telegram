# Production closure checklist

This checklist records the production-readiness evidence expected for the current `main` branch.

## P0 repository proof

- [ ] `git status --short` is clean on the server.
- [ ] `python -m pytest -q` passes.
- [ ] `APP_ENV=prod VALIDATOR_RELEASE_MODE=1 PYTHONDONTWRITEBYTECODE=1 python scripts/validate_project.py` passes.
- [ ] `APP_ENV=prod PYTHONDONTWRITEBYTECODE=1 python scripts/smoke.py` passes.
- [ ] `sudo systemctl status metrotherapy.service --no-pager -l` shows `active (running)`.
- [ ] `curl -i http://127.0.0.1:8082/healthz` returns `HTTP/1.1 200 OK` and `ok=true`.

## P0 YooKassa payment/package proof (VK, MAX and web)

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
  --source web
```

## P0 Telegram Stars proof

- [ ] Telegram package buttons show the current `XTR` price and open a native Stars flow.
- [ ] The Stars button opens a native `XTR` invoice with an empty `provider_token`.
- [ ] No Telegram tariff, gift or callback surface exposes an external YooKassa checkout.
- [ ] `/pay/yookassa?source=telegram` is rejected even when a legacy environment flag attempts to enable it.
- [ ] Disabling Stars disables the Telegram purchase path rather than silently falling back to an external digital-goods payment.
- [ ] `/terms` opens on the configured payment host and discloses that one Star is not one ruble.
- [ ] Each invoice amount matches the current canonical Stars pricing mode.
- [ ] A successful Stars payment creates one payment row and grants the package exactly once.
- [ ] A duplicate `successful_payment` does not grant practices or premium access twice.
- [ ] `/refundstars <charge_id>` previews the entitlement reversal without calling Telegram.
- [ ] `/refundstars <charge_id> CONFIRM` refunds through Telegram and revokes only unused access.

## P0 messenger proof

- [ ] Telegram polling runtime is active and not conflicting with another bot instance.
- [ ] Messenger webhook runtime is active.
- [ ] Telegram package buttons open the native Stars terms/invoice flow in a live chat; no external digital-goods checkout is offered.
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

## Remaining live proof

Repository and CI checks do not prove that the current VPS environment, Telegram bot token and provider accounts are deployed correctly. Run the production gate and the live Telegram/VK/MAX payment checks after deployment.
