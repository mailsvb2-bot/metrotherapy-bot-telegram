# Unified messenger contracts

This branch is a P2 architecture branch. It must not replace the current working runtime until parity is proven.

## Goal

Create one canonical contract for Telegram, VK and MAX message surfaces without introducing a second messenger brain.

The current production owners remain:

- `runtime/messenger_webhooks.py` for messenger webhook ingress;
- Telegram polling runtime for Telegram updates;
- `services/messenger/*` for channel-specific helpers;
- `services.messenger.outbound.SenderRegistry` for outbound delivery;
- health/runtime validation surfaces already proven on `main`.

## Non-goals

- Do not wholesale-merge old `interfaces/messaging/*` runtime from donor branches.
- Do not replace `runtime/messenger_webhooks.py` in this branch without parity tests.
- Do not create another routing/decision layer.
- Do not duplicate payment/package decision logic.
- Do not add auto-registering production webhook scripts without dry-run and explicit apply flags.

## Canonical message model

A future unified message surface should describe only presentation and delivery intent:

```text
MessengerMessage
- platform: telegram | vk | max
- external_user_id: str
- text: str
- buttons: list[MessengerButton]
- media: optional media descriptor
- metadata: immutable diagnostic context
```

```text
MessengerButton
- label: str
- kind: callback | url
- payload: str
```

The model must not decide business flow outcomes. It may only carry already-decided payloads from canonical services.

## Required parity tests

Before any runtime switch:

- Telegram package buttons preserve current YooKassa URLs.
- VK package links preserve source/user/package metadata.
- MAX package links preserve source/user/package metadata.
- Score buttons use the same numeric scale across Telegram/VK/MAX.
- Audio delivery fallback remains idempotent.
- Premium video delivery works through existing `SenderRegistry`.
- Unknown/invalid messenger payloads fail closed and are observable.

## Acceptance gates

This branch may be merged only when:

1. `python -m pytest -q` passes.
2. `APP_ENV=prod VALIDATOR_RELEASE_MODE=1 PYTHONDONTWRITEBYTECODE=1 python scripts/validate_project.py` passes.
3. `APP_ENV=prod PYTHONDONTWRITEBYTECODE=1 python scripts/smoke.py` passes.
4. No new runtime ingress owner is introduced.
5. No second payment/package/funnel decision source is introduced.
6. Admin/control-plane visibility exists for messenger preflight and delivery problems.

## Safe extraction order

1. Add pure contract dataclasses.
2. Add render-only adapters for current UI surfaces.
3. Add parity tests against existing Telegram/VK/MAX outputs.
4. Add observability-only preflight report.
5. Only then consider moving code out of `runtime/messenger_webhooks.py` behind tests.
