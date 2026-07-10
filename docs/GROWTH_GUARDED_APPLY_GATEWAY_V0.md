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
growth_apply_confirmations
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

## Review permission

Read-only Growth access and review access are separate permissions.

Sensitive review callbacks require:

```text
admin:growth:apply:review
```

A non-superadmin must have an explicit `allowed=1` permission row. The legacy read-navigation behavior where no permission rows means unrestricted access does not apply to this write boundary.

Superadmins remain allowed through the immutable environment-configured superadmin list.

## Two-step confirmation

Review uses two independent steps:

```text
prepare approve/reject
        ↓
server creates one-time confirmation challenge
        ↓
final confirm/cancel callback
```

The confirmation challenge is:

- bound to one request;
- bound to one decision;
- bound to one admin ID;
- stored only as a SHA-256 token hash;
- valid for a short TTL;
- single use;
- cancelled when a newer challenge is created for the same admin/request.

The raw token exists only in Telegram callback data. Callback values remain below Telegram's 64-byte limit.

The policy is evaluated when the confirmation is prepared and evaluated again inside the Gateway when the final decision is consumed. A kill-switch or limit change between the two clicks therefore blocks approval.

A consumed confirmation cannot be replayed. If the final policy evaluation fails, the request remains `pending_review`, `dispatch_allowed` remains zero, and a new confirmation must be prepared.

## UI scope

The Growth admin panel exposes:

- a read-only Guarded Apply overview;
- request details;
- prepare approval/rejection controls only for authorized reviewers;
- a separate final confirmation screen;
- explicit cancel.

There is no callback containing or invoking:

```text
execute
dispatch
flush
send
```

Approval and rejection only transition review state and write audit history.

## Readiness isolation

These tables are optional Growth infrastructure and are not part of the primary payment/delivery P0 readiness set.

Missing Gateway or confirmation schema degrades the Growth review surface but must not block:

- payments;
- access grants;
- Telegram handlers outside Growth review;
- scheduler liveness;
- public runtime health.
