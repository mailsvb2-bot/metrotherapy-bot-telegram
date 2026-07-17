# Audit backlog closure

This change set closes the implementation gaps tracked in issues #134, #135 and #136.

## YooKassa refunds

- `refund.succeeded` is verified through the provider refund API.
- Refund id, payment id, status, amount and currency are matched before persistence.
- Refunds have a dedicated idempotent ledger.
- Partial refunds are recorded without guessing proportional token revocation.
- A cumulative full refund automatically revokes only a fully unused exact payment lot.
- Used/reserved access, claimed gifts, delivered premium content and active consultations become `action_required` with preserved evidence.

## Messenger delivery

- VK and MAX have independently configurable worker pools.
- Only the oldest non-terminal delivery in one user/platform stream can be claimed.
- Different user streams can run concurrently.
- Sent, dead-letter and completed webhook evidence use bounded retention batches.
- Health output exposes worker counts, throughput and cleanup counters.

## Privacy

- Every user-owned table must declare `erase`, `retain` or `anonymize` in a versioned manifest.
- Unknown ownership tables fail strict startup/CI validation.
- Export and erasure use the same manifest.
- Financial, refund, dispute and fulfilment facts remain explicit and versioned.
