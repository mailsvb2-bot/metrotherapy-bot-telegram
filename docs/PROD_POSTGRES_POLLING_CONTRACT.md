# Production contract: Postgres + Telegram polling

This deployment has two hard production rules.

## 1. Telegram stays on polling

Production must keep Telegram updates on polling. Telegram webhook code remains in the repository only as a development or migration capability. It is not the production ingress contract.

MAX, VK, payment checkout and media links may still use the local messenger HTTP runtime. That does not change Telegram update delivery: Telegram remains polling.

## 2. Production storage is Postgres-only

Production must run with Postgres as the active database engine and with a configured Postgres database URL.

SQLite remains allowed for local development and hermetic tests only. It is not a production fallback.

The reason is operational, not cosmetic: scheduler locks, payment idempotency, entitlement grants, disaster recovery and restore drills need one durable shared database.

## 3. Release gate

Before calling the deployment production-ready, run the non-bypassable gate on the target server:

```bash
python scripts/production_gate.py
```

Expected final marker:

```text
PRODUCTION_GATE_OK
```

This gate must run against the real server environment and requires a safe non-production restore target for the Postgres restore drill.

## 4. What should fail now

A prod process must fail closed when any of these are true:

- production starts with SQLite as the active engine;
- production starts without a configured database URL;
- production starts with a non-Postgres database URL scheme;
- production starts with Telegram webhook enabled;
- production starts with a Telegram transport other than polling.
