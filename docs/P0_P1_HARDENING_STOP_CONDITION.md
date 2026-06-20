# Stop condition

The hardening branch should be considered complete only when all of the following are true:

- GitHub CI is green.
- The PR is merged into `main`.
- Production env includes `PAYMENT_CHECKOUT_SIGNING_KEY`.
- Production env includes YooKassa credentials and webhook header value.
- `python scripts/production_gate.py` is green on the server.

Until then, the branch is a production-hardening candidate, not final live proof.
