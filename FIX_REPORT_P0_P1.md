# Fix report — P0/P1 architecture pass

## Scope

This archive applies a safe first remediation pass for the P0/P1 findings from the repository audit.

## Changed

1. `core/engine.py`
   - Replaced the long `if/elif job.job_type` execution chain with a canonical `_job_handlers()` registry.
   - Added `_execute_job()` as a single execution boundary for claimed jobs.
   - Routed job execution through `DecisionCore` with a policy token before running side effects.
   - Removed unreachable `demo_sent` logging code after `return` in `_demo_send()`.
   - Changed unexpected engine job crashes from immediate silent completion to bounded retry (`ENGINE_JOB_CRASH_MAX_RETRIES`, default `3`) before final terminal marking.

2. `core/ai/decision_core.py`
   - Added explicit `engine_job_execute` policy.
   - Added allow/deny decisions for known vs unknown engine job types.
   - Added `engine_job_registry_v1` policy metadata for observability.

3. `services/validators/architecture.py`
   - Added `validate_engine_job_dispatch_contract()` guardrail.
   - The architecture validator now fails if the engine returns to job-type branching instead of registry dispatch.

4. `tests/test_architecture_contracts.py`
   - Added regression tests for the new engine dispatch guardrail.
   - Added a regression test for the removed unreachable demo-send code.
   - Added a DecisionCore policy test for known/unknown engine job execution.

## Validation run locally

Passed:

```bash
python -m compileall -q core services tests
python -m scripts.validate_project
pytest -q tests/test_architecture_contracts.py -k 'not fsm_states_have_single_identity'
pytest -q tests/test_jobs_contracts.py tests/test_runtime_contract.py
```

Known local environment limitation:

```bash
pytest -q
```

Full pytest collection could not complete in this sandbox because runtime dependencies are not installed here, starting with `aiogram` (`ModuleNotFoundError: No module named 'aiogram'`). This is an environment limitation of the sandbox, not a failure introduced by this patch. Run full CI/GitHub Actions or install `requirements.txt` + `requirements-dev.txt` before the final merge.

## Not fully closed in this pass

The following audit findings are intentionally not mass-edited in this single pass because they need broader CI-backed work:

- Full tightening of Ruff/Mypy/Bandit policy.
- Large-scale reduction of the broad-exception allow-list.
- Full removal of SQLite-to-Postgres compatibility magic.
- Full product terminology unification across tariffs/packages/practices/tokens.

These should be handled as separate PR waves to avoid production regression.
