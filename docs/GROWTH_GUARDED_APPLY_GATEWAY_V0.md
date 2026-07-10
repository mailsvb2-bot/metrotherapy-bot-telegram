# Growth Guarded Apply Gateway v0

Guarded Apply Gateway v0 is an approval and audit boundary for future advertising actions.

It is intentionally **not an execution engine**.

## State machine

```text
pending_review -> approved
pending_review -> rejected
pending_review -> expired
```

Terminal states cannot transition again.

`approved` means that a request passed the configured policy and was reviewed by an administrator. It does not mean that an external advertising platform was changed.

## Hard locks

The database enforces:

```text
mode = approval_only
dispatch_allowed = 0
```

There is no `executed` status and no sender/adapter/flush function in v0.

## Supported action types

```text
budget_change
campaign_pause
campaign_resume
creative_rotate
```

Unknown actions are rejected by the pure policy core.

## Policy

Default policy is fail-closed:

- global kill-switch enabled;
- budget changes disabled;
- pause/resume disabled;
- creative rotation disabled;
- requester cannot approve their own request;
- critical-risk requests cannot be approved.

Environment settings:

```text
GROWTH_APPLY_KILL_SWITCH=1
GROWTH_APPLY_MAX_BUDGET_DELTA_MINOR=0
GROWTH_APPLY_MAX_BUDGET_DELTA_PCT=0
GROWTH_APPLY_ALLOW_PAUSE_RESUME=0
GROWTH_APPLY_ALLOW_CREATIVE_ROTATE=0
GROWTH_APPLY_REQUIRE_DISTINCT_APPROVER=1
```

Invalid numeric values fail closed to zero limits.

## Persistence

Tables:

```text
growth_apply_requests
growth_apply_audit
```

Request creation, state transition and audit insertion are transactional.

Every state transition creates an immutable audit event with:

- actor;
- before status;
- after status;
- reason/details;
- UTC timestamp.

TTL expiration is committed and audited before the caller receives the `growth_apply_request_expired` result.

## Idempotency

A semantic request key is derived from:

```text
action_type + target_platform + target_ref + normalized payload
```

Repeated creation of the same proposal returns the existing request and does not duplicate the initial audit event.

## UI scope

The Growth admin panel currently exposes a read-only Guarded Apply report.

v0 does not expose approve/reject callbacks. Before write UI is added, it must have:

- a dedicated permission separate from read-only Growth access;
- explicit confirmation UX;
- actor identity tests;
- concurrent transition tests;
- audit contract tests;
- no route that can dispatch an external action.

## Readiness isolation

These tables are optional Growth infrastructure and are not part of the primary payment/delivery P0 readiness set.

Missing Gateway schema degrades the report but must not block:

- payments;
- access grants;
- Telegram handlers;
- scheduler liveness;
- public runtime health.
