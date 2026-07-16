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

MAX requires the Russian Trusted Root CA and Russian Trusted Sub CA for `platform-api2.max.ru`.

The certificate copies used by deployment are vendored in the repository:

```text
deploy/certs/russian_trusted_root_ca.crt
deploy/certs/russian_trusted_sub_ca.crt
```

They originate from the public Госуслуги certificate source, but production deploy does not depend on downloading them at runtime. The installer validates the SHA-256 fingerprint of the parsed DER certificate, which is stable across PEM line-ending or formatting changes:

```text
Russian Trusted Root CA
D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31

Russian Trusted Sub CA
BB:BD:E2:10:3E:79:0B:99:9E:C6:2B:D0:3C:F6:25:A5:A2:E7:C3:16:E1:0A:FE:6A:49:0E:ED:EA:D8:B3:FD:9B
```

Production deploy runs:

```bash
scripts/install_max_trust.sh
```

Before changing the operating-system trust store, the installer verifies:

- the DER fingerprints above;
- certificate subjects;
- remaining validity;
- self-validation of the Root CA;
- the Root → Sub CA chain.

Debian/Ubuntu use `update-ca-certificates`; RHEL-family systems use `update-ca-trust`. TLS verification is never disabled. After installation the script performs a real verified TLS request to `platform-api2.max.ru`.

The deployment marker is created only after trust installation and the normal deploy/restart/health cycle succeed:

```text
/var/lib/metrotherapy/deploy-migrations/max-mincifry-trust-v1.applied
```

When trust installation fails, the worker publishes one sanitized commit beginning with `[max-trust-install-result]` and then stops. That result commit is explicitly ignored by the next worker invocation, preventing a failure-report loop.

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
