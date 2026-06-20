# Release closure map

This file tracks the production-readiness topics that must stay closed after the Postgres and Telegram polling migration.

## P0 closed

- Production runtime contract exists and is executable.
- Telegram production transport is polling-only.
- Production storage is Postgres-only.
- Server production gate is the release source of truth when GitHub Actions are unavailable.
- Postgres restore drill is part of the hard gate.
- Health and readiness are part of the hard gate.

## P1 now guarded

- Legacy SQLite cleanup has a conservative archive runbook and operator tool.
- Scheduler job claiming uses a native Postgres concurrent claim path.
- Production gate includes a Postgres job concurrency probe.
- Production gate includes a no-send auto-audio load dry-run for 150 synthetic users by default.
- Backup freshness is visible and can turn DR status red when backups are stale.
- Admin release report includes a runtime contract summary.

## P1 remaining operator work

- Run the legacy SQLite archive step on the server after a green production gate.
- Re-run production gate and confirm storage audit becomes GREEN.
- Keep archived SQLite until rollback policy allows removal.

## P2 next hardening

- Expand native Postgres paths beyond scheduler claim into more payment and delivery critical paths.
- Add controlled real-provider payment proof for YooKassa outside synthetic probes.
- Add alerting for stale backups, failed probes, stale locks, payment problems and scheduler errors.
