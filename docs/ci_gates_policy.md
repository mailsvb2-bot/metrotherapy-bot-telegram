# CI gates policy

The project separates automatic pull-request gates from manual advisory gates.

## Automatic gates

Automatic gates are lightweight checks that are safe to run on the current runner:

- release hygiene
- compile checks
- Ruff quality gate
- smoke check
- strict guardrails
- disaster recovery proof

CI virtual environments must be created under the runner temporary directory, outside the checkout tree. This prevents generated files from being scanned by release hygiene.

## Manual advisory gates

Some checks are useful but require a dedicated test environment. They are manual-only until that environment exists:

- PostgreSQL smoke
- Python 3.13 compatibility

These advisory gates should become automatic again only after the required isolated runner environment is available.
