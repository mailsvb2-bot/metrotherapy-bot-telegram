# MAX webhook setup

The runtime protects `/webhooks/max` with `MAX_WEBHOOK_SECRET`.

Inbound MAX requests are accepted only when the request provides the configured secret through one of these supported channels:

- `X-Max-Webhook-Secret`
- `X-Webhook-Secret`
- `X-Metrotherapy-Webhook-Secret`
- `?secret=...`
- JSON payload field `secret`

## Dry-run

Load the production environment first, then print the protected webhook URL:

```bash
set -a
. /etc/metrotherapy/metrotherapy.env
set +a
python scripts/configure_max_webhook.py --json
```

The generated URL has this shape:

```text
https://<MESSENGER_PUBLIC_BASE_URL>/webhooks/max?secret=<MAX_WEBHOOK_SECRET>
```

## Apply

The public MAX webhook registration endpoint was intentionally not hard-coded. Set the official registration endpoint in `MAX_SET_WEBHOOK_URL`, then run:

```bash
set -a
. /etc/metrotherapy/metrotherapy.env
set +a
python scripts/configure_max_webhook.py --apply --json
```

Optional registration variables:

```env
MAX_SET_WEBHOOK_URL=
MAX_SET_WEBHOOK_METHOD=POST
MAX_SET_WEBHOOK_TOKEN_HEADER=Authorization
MAX_SET_WEBHOOK_AUTH_SCHEME=raw
MAX_SET_WEBHOOK_TIMEOUT_SEC=20
```

`MAX_SET_WEBHOOK_AUTH_SCHEME` values:

- `raw`: sends the bot token as the header value.
- `bearer`: sends `Bearer <token>`.
- `token`: sends `Token <token>`.

If the official MAX API supports registering a URL with a query string, prefer the generated `?secret=...` URL. If it supports headers instead, keep the runtime secret validation and configure MAX to send one of the supported headers.
