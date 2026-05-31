# Current server systemd contract

This document records the effective production unit currently used on the Timeweb
server for the Metrotherapy bot. It exists to prevent drift between repository
expectations and the real service that is restarted during deploys.

The canonical repository service template is still `deploy/metrotherapy.service`.
The current server runs from `/root/metrotherapy` and uses an override file.

## Effective unit shape

```ini
# /etc/systemd/system/metrotherapy.service
[Unit]
Description=Metrotherapy Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/metrotherapy
EnvironmentFile=/root/metrotherapy/.env
ExecStart=/root/metrotherapy/.venv/bin/python /root/metrotherapy/main.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target

# /etc/systemd/system/metrotherapy.service.d/override.conf
[Service]
EnvironmentFile=
EnvironmentFile=/etc/metrotherapy/metrotherapy.env
Environment=MPLCONFIGDIR=/var/lib/metrotherapy/mplcache
WorkingDirectory=/root/metrotherapy
ExecStart=
ExecStart=/root/metrotherapy/.venv/bin/python /root/metrotherapy/main.py
Restart=always
RestartSec=5
```

## Required post-change commands

```bash
sudo systemctl daemon-reload
sudo systemctl restart metrotherapy.service
sleep 8
sudo systemctl show metrotherapy.service -p Environment
curl -sS http://127.0.0.1:8082/healthz
curl -sS http://127.0.0.1:8082/readyz
```

Expected proof:

- `Environment=MPLCONFIGDIR=/var/lib/metrotherapy/mplcache`
- `/healthz` returns `ok: true`
- `/readyz` returns `ok: true`
- readiness has `db_ready`, `schema_ready`, `scheduler_ready`, `webhook_ready` all true

## Production invariants

- Runtime source directory: `/root/metrotherapy`
- Environment file: `/etc/metrotherapy/metrotherapy.env`
- Python entrypoint: `/root/metrotherapy/main.py`
- Health endpoint: `127.0.0.1:8082`
- Messenger webhook runtime: `8081`
- Telegram transport: polling
- Database engine: Postgres
- Matplotlib cache: `/var/lib/metrotherapy/mplcache`

Do not remove the override until the server has been migrated to the repository
service path or `/opt/metrotherapy` is made the real runtime path.
