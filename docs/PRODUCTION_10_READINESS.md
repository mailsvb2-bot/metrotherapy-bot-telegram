# Production 10/10 Readiness Evidence

Date: 2026-06-21  
Repository: `mailsvb2-bot/metrotherapy-bot-telegram`

## Server gate

The production server gate completed successfully with the canonical stop-condition:

```text
PRODUCTION_GATE_OK
```

Verified probes from the server run:

- runtime contract: OK
- pytest: `397 passed`
- production validator: OK
- smoke: OK
- storage legacy audit: GREEN, active engine Postgres
- disaster recovery status: GREEN
- scheduler job probe: OK
- auto-audio dry-run probe: OK
- payment entitlement probe: OK
- synthetic user journey E2E: OK
- Telegram live smoke: OK, polling transport, no webhook URL
- Postgres restore drill: OK
- `/health`: OK
- `/readyz`: OK
- Postgres job concurrency probe: OK
- auto-audio load dry-run: 150 users, 16 concurrency, zero failures

## Final hardening added after the gate

This branch closes the remaining non-blocking audit items:

1. `jobs.job_key` is enforced as a database invariant through a dedicated migration and test.
2. Postgres job enqueue now uses native `ON CONFLICT` against the unique `job_key` index.
3. `ALLOW_UNGUARDED_PROD` is rejected by the production validator instead of being treated as an emergency runtime switch.
4. CI expands the type and security gates to cover scheduler/runtime guardrail code, not only payment code.

## Release rule

A production release is acceptable only when both conditions are true:

1. GitHub CI passes on the release commit or PR.
2. The server command below ends with `PRODUCTION_GATE_OK`.

```bash
cd /root/metrotherapy && \
set -a && [ -f /etc/metrotherapy/metrotherapy.env ] && . /etc/metrotherapy/metrotherapy.env && set +a && \
source .venv/bin/activate && \
python scripts/production_gate.py 2>&1 | tee /tmp/metrotherapy_production_gate_$(date +%Y%m%d_%H%M%S).log
```
