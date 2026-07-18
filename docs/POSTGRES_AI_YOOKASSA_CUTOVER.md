# Postgres, AI and YooKassa cutover

Canonical constraints:

- Telegram stays in polling mode.
- Messenger webhook runtime may stay enabled for VK/MAX/YooKassa.
- AI is admin/marketing only; user-facing therapy scopes stay forbidden.
- Project root must not contain `.env` in release mode. Use `/etc/metrotherapy/metrotherapy.env`.

## 1. Baseline before cutover

```bash
cd /root/metrotherapy
git status -sb
APP_ENV=prod VALIDATOR_RELEASE_MODE=1 VALIDATOR_GUARDRAILS_STRICT=1 python scripts/validate_project.py
APP_ENV=prod VALIDATOR_RELEASE_MODE=1 VALIDATOR_GUARDRAILS_STRICT=1 python scripts/smoke.py
python -m pytest -q
curl -sS http://127.0.0.1:8082/healthz
curl -sS http://127.0.0.1:8082/readyz
```

Expected: all checks return zero and health/readiness contain `ok: true`.

## 2. Keep Telegram polling

The production env must keep:

```env
TELEGRAM_TRANSPORT=polling
TELEGRAM_WEBHOOK_ENABLED=0
MESSENGER_WEBHOOK_ENABLED=1
```

Do not enable Telegram webhook during this cutover.

## 3. Prepare Postgres

Install Postgres and create a dedicated database/user on the server or managed Postgres provider.

Required application env:

```env
METRO_DB_ENGINE=postgres
DATABASE_URL=<postgres connection string>
```

Before switching systemd, verify the driver and connectivity:

```bash
cd /root/metrotherapy
set -a
source /etc/metrotherapy/metrotherapy.env
set +a
METRO_DB_ENGINE=postgres python scripts/check_postgres.py
```

## 4. Initialize Postgres schema

```bash
cd /root/metrotherapy
set -a
source /etc/metrotherapy/metrotherapy.env
set +a
METRO_DB_ENGINE=postgres python - <<'PY'
from services.schema_core import init_db
init_db()
print('postgres_schema_ok')
PY
```

## 5. Data migration note

The current repo contains a Postgres compatibility layer and schema initialization, but data cutover from the live SQLite file should be done conservatively:

1. Stop the service.
2. Copy `/var/lib/metrotherapy/data.db` to a timestamped backup.
3. Export SQLite tables.
4. Import into Postgres.
5. Run validator/smoke against Postgres.
6. Start service only after checks pass.

Do not delete the SQLite backup after cutover.

## 6. Enable AI safely

AI can be enabled only for admin/marketing scopes. User-facing therapy, diagnosis, medical advice and treatment promises remain forbidden by policy.

Provider examples:

```env
AI_ENABLED=1
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=<secret>
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
OPENAI_THINKING=disabled
```

or use Yandex/GigaChat/OpenAI-compatible envs already supported by `services.ai.providers.router`.

After changing env:

```bash
systemctl daemon-reload
systemctl restart metrotherapy.service
curl -sS http://127.0.0.1:8082/healthz
```

Expected AI fields:

- `ai_enabled: true`
- `ai_provider_configured: true`
- `ai_user_therapy_allowed: false`

## 7. YooKassa reconciliation probe

The probe never contacts YooKassa and never charges money. It verifies the local reconciliation and entitlement path with synthetic identifiers only.

Dry-run first:

```bash
cd /root/metrotherapy
set -a
source /etc/metrotherapy/metrotherapy.env
set +a
python scripts/probe_payment_reconciliation_live.py \
  --package practice_start_7 \
  --source vk
```

Dry-run performs no schema initialization, no probe-ledger write and no application-table mutation. Expected report fields:

- `ok: true`
- `mode: "dry_run"`
- `applied: false`
- `database_touched: false`

Controlled mutation probe:

```bash
python scripts/probe_payment_reconciliation_live.py \
  --package practice_start_7 \
  --source vk \
  --apply-webhooks \
  --allow-live-db-mutation
```

The two mutation flags are inseparable. Supplying only one fails closed before the probe touches the database. A unique negative user id from the reserved synthetic namespace is generated automatically. To make the id explicit, use a value from `-999999999` through `-900000000`, for example:

```bash
python scripts/probe_payment_reconciliation_live.py \
  --package practice_start_7 \
  --source vk \
  --user-id -910000301 \
  --apply-webhooks \
  --allow-live-db-mutation
```

The mutation probe always replays the same synthetic webhook twice. No separate duplicate flag exists or is needed.

Expected:

- first webhook is inserted/applied;
- duplicate webhook is not inserted again;
- wallet/token delta matches package token count;
- canonical account and payment-side rows are observed;
- cleanup removes all synthetic payment, wallet, entitlement, outbox, consultation and account rows;
- `residual_rows` is zero;
- report returns `ok: true`.

Use `--keep-artifacts` only together with both mutation flags when an operator explicitly needs retained synthetic evidence. The report then uses `cleanup_status: "kept"` and the probe ledger remains visibly non-clean until the artifacts are removed.

## 8. Final restart and probes

```bash
systemctl daemon-reload
systemctl restart metrotherapy.service
systemctl status metrotherapy.service --no-pager
journalctl -u metrotherapy.service -n 100 --no-pager
curl -sS http://127.0.0.1:8082/healthz
curl -sS http://127.0.0.1:8082/readyz
```
