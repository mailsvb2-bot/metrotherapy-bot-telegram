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

MAX requires the Russian Trusted Root CA and Russian Trusted Sub CA for `platform-api2.max.ru`. Production deploy runs:

```bash
scripts/install_max_trust.sh
```

The installer downloads the two certificates from the public Госуслуги certificate host over verified HTTPS and validates their immutable SHA-256 values before changing the trust store:

```text
Russian Trusted Root CA
936a43fea6e8e525bcc0f81acd9c3d21b4fc4b9b68acea7906d698005afc6504

Russian Trusted Sub CA
f0ae589f36774f29ef3648f7984b08d42fcce6f1ffeeb6236d773daeb2744ea6
```

It additionally validates certificate subjects, expiration, the root/subordinate chain and a real TLS handshake to `platform-api2.max.ru`. Debian/Ubuntu use `update-ca-certificates`; RHEL-family systems use `update-ca-trust`. TLS verification is never disabled.

The deployment marker is created only after the trust installation and the normal deploy/restart/health cycle succeed:

```text
/var/lib/metrotherapy/deploy-migrations/max-mincifry-trust-v1.applied
```

`MAX_CA_BUNDLE` remains available only for a deliberately managed dedicated PEM bundle. Normally leave it empty so all MAX runtime calls use the updated operating-system trust store.

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
