# RewardEngine production indexing

RewardEngine evaluates recent `decision_made` events on a strict scheduler budget. On a large PostgreSQL `events` table, the candidate query must not fall back to a full-table scan.

Production schema initialization therefore ensures two online indexes before the runtime scheduler is allowed to start:

- `idx_events_reward_candidates_v1` — a partial covering index over the effective event timestamp and id for rows where `name='decision_made'` and `decision_id` is present;
- `idx_decision_rewards_decision_window_v1` — the exact `(decision_id, window_sec)` lookup used by the idempotency anti-join.

PostgreSQL builds these indexes with `CREATE INDEX CONCURRENTLY` on a dedicated autocommit connection so the live release can continue serving writes while an immutable candidate prepares its schema. Interrupted or invalid indexes are detected, dropped concurrently, and rebuilt on the next attempt. The build remains bounded by `REWARD_INDEX_STATEMENT_TIMEOUT_SEC` (default 480 seconds) and `REWARD_INDEX_LOCK_TIMEOUT_SEC` (default 10 seconds).

SQLite keeps the existing lightweight schema path and does not run the online-index protocol.

The scheduler readiness threshold and the five-second production RewardEngine owner budget are intentionally unchanged. The purpose of this work is to remove the pathological PostgreSQL access path, not to hide it behind longer timeouts or weaker readiness.
