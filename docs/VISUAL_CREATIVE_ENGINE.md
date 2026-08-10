# Visual Creative Engine

Metrotherapy uses Visual Creative Engine as an optional staff/marketing capability. It is deliberately outside the therapeutic decision core: visual generation cannot change therapy selection, user state, payments, access, scheduling, or any other user-facing business decision.

## Architecture

The bot contains only three integration layers:

1. `services/visual_creative_gateway.py` — bounded HTTP client for the independent gateway.
2. `services/metrotherapy_visual_creatives.py` — Metrotherapy-specific marketing brief and safety/brand policy.
3. `handlers/admin_visual_creatives.py` — staff commands and Telegram delivery.

The provider gateway is deployed separately. That separation keeps provider switching (YandexART, GigaChat, OpenAI, Runway, self-hosted models) out of the bot's domain core and lets each country/deployment use its own provider policy without branching Metrotherapy business logic.

## Staff commands

- `/creative_image <concept>`
- `/creative_video <concept>`
- `/creative_status <job_id>`

The commands are available to superadmins and to `admin`/`marketing` staff whose existing scoped-permission policy permits `admin:visual:creative`. The permission is also exposed in the existing admin permission list.

Each request is scoped to `staff:<telegram-user-id>`. Idempotency uses the staff id, Telegram chat id, message id, and visual kind. The chat id is required because Telegram message ids are not globally unique.

## Runtime configuration

See `deploy/visual-creative.env.example`. At minimum, configure:

- `VISUAL_GATEWAY_URL`
- `VISUAL_GATEWAY_TOKEN`
- `VISUAL_DEPLOYMENT_COUNTRY`

Do not commit real gateway tokens. The gateway URL parser rejects embedded credentials, query strings, fragments, malformed ports, and non-HTTP(S) schemes. JSON/media reads are bounded and downloaded files are MIME-checked before delivery.

## Media lifecycle

Generated media remains durable at the gateway. Metrotherapy downloads only a temporary local copy for Telegram upload and deletes that copy after the upload attempt, including failed Telegram sends. This prevents generated media from accumulating indefinitely in the bot runtime directory.

## Provider independence

The Metrotherapy client does not select a concrete provider by default. It sends the deployment country and the creative brief to the gateway. Provider routing, credentials, quotas, idempotency reservations, retry/failover policy, and model configuration remain gateway responsibilities.

The standalone gateway supplied with the integration archive must therefore be deployed as its own service/repository rather than copied into the Metrotherapy monolith.

## Validation contract

The integration must pass the repository's existing CI without lowering or bypassing any gate. In particular, existing regression, coverage/branch-coverage ratchets, Ruff runtime-danger checks, critical mypy/Bandit checks, dependency audit, and PostgreSQL payment/concurrency probes remain authoritative.

Focused tests additionally cover gateway request scoping, response/media bounds, MIME validation, fail-closed URL parsing, staff authorization, cross-chat idempotency, router registration, and temporary-file cleanup.
