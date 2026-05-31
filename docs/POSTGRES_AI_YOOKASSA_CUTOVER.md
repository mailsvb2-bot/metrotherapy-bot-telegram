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

Dry-run first:

```bash
cd /root/metrotherapy
set -a
source /etc/metrotherapy/metrotherapy.env
set +a
python scripts/probe_payment_reconciliation_live.py --package practice_start_7 --source vk --user-id 990000001
```

Controlled mutation probe:

```bash
python scripts/probe_payment_reconciliation_live.py \
  --package practice_start_7 \
  --source vk \
  --user-id 990000001 \
  --apply-webhooks \
  --allow-live-db-mutation \
  --duplicate
```

Expected:

- first webhook is inserted/applied;
- duplicate webhook is not inserted again;
- wallet/token delta matches package token count;
- report returns `ok: true`.

## 8. Final restart and probes

```bash
systemctl daemon-reload
systemctl restart metrotherapy.service
systemctl status metrotherapy.service --no-pager
journalctl -u metrotherapy.service -n 100 --no-pager
curl -sS http://127.0.0.1:8082/healthz
curl -sS http://127.0.0.1:8082/readyz
```
