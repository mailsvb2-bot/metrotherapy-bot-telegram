# Live proof checklist after P0/P1 hardening

This checklist is intentionally separate from code changes because it must be run on the real server with real environment values.

## 1. Preflight

```bash
python scripts/check_release_hygiene.py
python -m compileall services scripts handlers core runtime config app.py main.py
python scripts/smoke.py
python -m pytest -q -p no:cacheprovider
python scripts/validate_project.py
python scripts/check_ruff.py
```

## 2. Hardened payment proof

Create a checkout link from Telegram/VK/MAX and confirm that the URL contains `intent=`.

Open the URL and confirm YooKassa redirects normally.

Send a webhook without the configured header and confirm it returns `403`.

Send a provider fixture only in non-production. In production, rely on the YooKassa callback and check that grants are applied only after provider verification.

## 3. Health and readiness

```bash
curl -s http://127.0.0.1:8082/health
curl -s http://127.0.0.1:8082/readyz
```

`/readyz` must show `ok: true` and include the expanded table list.

## 4. Final production gate

```bash
python scripts/production_gate.py
```

Only a green result from this command is sufficient proof for broad production readiness.
