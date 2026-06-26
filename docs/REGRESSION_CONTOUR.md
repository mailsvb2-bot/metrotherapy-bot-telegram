# Regression contour

This project has one canonical regression gate:

```bash
python scripts/regression_gate.py
```

The GitHub CI workflow runs this same command after dependency installation. Do not add a separate
"almost the same" check path: if a release-critical check is needed, add it to `scripts/regression_gate.py`
so local verification and GitHub Actions stay aligned.

## What the gate locks

1. Release hygiene before checks: no caches, runtime databases, logs or packaging fragments in the tree.
2. Project-surface compilation: syntax and import-surface regressions are caught before runtime tests.
3. Hermetic smoke with polling-only Telegram production contract.
4. Full pytest suite as the user-functionality regression contract.
5. Strict validator with production guardrails enabled.
6. Ruff runtime-danger gate for parser errors and undefined names.
7. Release hygiene after checks, so tests cannot leave shippable garbage behind.

## Rules for future changes

- Every transport change must add or update a focused contract test for Telegram/VK/MAX parity.
- Every payment, token or access change must add a prod-guardrail regression test.
- Every DB compatibility change must add a SQLite/Postgres contract test where the behavior differs.
- A PR is not releasable until `REGRESSION_GATE_OK` is visible locally or in CI.
