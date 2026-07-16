# MAX webhook setup

MAX production uses the official webhook subscription API and the API2 domain.

## Required production variables

```env
MAX_WEBHOOK_ENABLED=1
MAX_BOT_TOKEN=...
MAX_WEBHOOK_SECRET=...
MAX_API_BASE_URL=https://platform-api2.max.ru
MESSENGER_PUBLIC_BASE_URL=https://your-public-domain.example
```

`MAX_WEBHOOK_SECRET` must contain 5–256 characters from `A-Z`, `a-z`, `0-9`, `_`, `-`.

The runtime validates the official webhook header:

```text
X-Max-Bot-Api-Secret
```

Historical internal header aliases remain accepted for compatibility, but new registrations must use the official `secret` field of `POST /subscriptions`. Do not put the webhook secret into the URL.

## TLS trust

MAX requires HTTPS with a trusted certificate chain. Python uses the operating-system trust store by default. Keep the server CA store current and include the required trusted certificate chain.

When the server deliberately uses a dedicated PEM bundle, set:

```env
MAX_CA_BUNDLE=/absolute/path/to/ca-bundle.pem
```

Do not disable TLS verification.

## Register or refresh the subscription

Load the server environment and run the canonical helper:

```bash
set -a
. /etc/metrotherapy/metrotherapy.env
set +a
python scripts/register_max_webhook.py
```

The helper:

- calls `https://platform-api2.max.ru` only;
- sends the bot token only in the API `Authorization` header;
- creates the subscription through `POST /subscriptions`;
- passes the webhook secret in the JSON `secret` field;
- verifies that `/webhooks/max` appears in active subscriptions;
- never prints the bot token or webhook secret.

The old query-secret registration helper was removed. Do not place webhook secrets into URLs, access logs or operational screenshots.

## Runtime contract

The public endpoint is:

```text
https://<MESSENGER_PUBLIC_BASE_URL>/webhooks/max
```

The endpoint returns HTTP 200 quickly. Processing and outgoing replies run asynchronously, while duplicate events are suppressed in the database.

Uploaded media is first registered through the authenticated `/uploads` API call. The returned upload URL receives the media bytes without forwarding `MAX_BOT_TOKEN`. Supported audio formats are sent directly; only unsupported formats require ffmpeg conversion. Provider-side `attachment.not.ready` responses use increasing configurable waits.
