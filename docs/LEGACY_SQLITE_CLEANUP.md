# Legacy SQLite cleanup runbook

After migration to Postgres, storage audit can stay YELLOW when an old SQLite artifact is still present on the server. This is not an active-storage failure when active storage is Postgres, repo-local SQLite is absent, and disallowed direct sqlite connections are zero.

The cleanup rule is simple: archive the old file, do not delete it directly.

## Required order

1. Run the production gate and continue only after the final OK marker.
2. Run the legacy SQLite archive tool in dry-run mode.
3. Apply the archive only when the dry-run plan is OK.
4. Run the production gate again.

## Expected result

After cleanup, storage legacy audit should move from YELLOW to GREEN while the final production gate marker remains OK.

## Safety rules

- Do not remove the SQLite artifact manually before a successful Postgres restore drill.
- Do not run cleanup if active storage is not Postgres.
- Do not point restore-drill URLs at production.
- Keep the archived SQLite file until rollback policy says it is safe to remove.
