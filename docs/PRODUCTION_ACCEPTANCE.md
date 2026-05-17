# Production acceptance checklist

This checklist is the final gate after pulling a release on the server and before increasing traffic, ads, or paid-user exposure.

## Automated gate

Run from the project root:

```bash
python scripts/production_acceptance.py
```

Expected result:

```text
PRODUCTION ACCEPTANCE: OK
```

The runner composes existing checks instead of creating a second validation brain:

- project compile check;
- full pytest;
- production readiness check;
- runtime observability check;
- local health and readiness probes;
- local messenger webhook health;
- VK/MAX webhook method-contract probes.

## Required manual live-flow checks

The automated gate does not send real Telegram/VK/MAX user messages and does not create real payments. Before declaring a release operationally accepted, manually verify:

1. Telegram `/start` opens the main menu.
2. Telegram demo flow: score before -> audio -> score after -> chart.
3. MAX user message reaches the webhook runtime and can run the same before/audio/after/chart flow.
4. VK user message reaches the webhook runtime and can run the same before/audio/after/chart flow.
5. Payment test uses the intended YooKassa test/live mode and produces the expected attribution/reconciliation record.
6. Admin view shows messenger/runtime/payment status without errors.

## Known non-blocking warnings for alpha/staging

These are warnings in the current single-server alpha/staging profile and must be closed before full production-grade operation:

- SQLite runtime DB instead of Postgres.
- Empty `VK_SECRET`.
- Empty live YooKassa reconciliation credentials.
- Runtime artifacts such as `.env`, local DB files and logs are present on the live server. They are allowed for live operation but forbidden in release archives.

## Hard stop conditions

Do not increase traffic or spend if any of these happens:

- `pytest` fails.
- `runtime_observability_check.py` fails.
- `/readyz` is not `200`.
- Telegram polling is not active when Telegram is configured for polling.
- Messenger webhook runtime is not active when VK/MAX are enabled.
- Recent journal output contains tracebacks for the current service PID.
- Payment test creates duplicate or unattributed payments.
