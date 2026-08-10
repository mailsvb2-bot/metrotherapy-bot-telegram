# Visual Creative Engine

Metrotherapy uses Visual Creative Engine as an optional staff/marketing capability. It is deliberately outside the therapeutic decision core: visual generation cannot change therapy selection, user state, payments, access, scheduling, or any other user-facing business decision.

## Architecture

The bot contains four integration layers:

1. `services/visual_creative_gateway.py` — bounded HTTP client for the independent gateway.
2. `services/visual_creative_capability.py` — enablement and configuration-readiness contract.
3. `services/metrotherapy_visual_creatives.py` — Metrotherapy-specific marketing brief and safety/brand policy.
4. `handlers/admin_visual_creatives.py` — staff commands and Telegram delivery.

The provider gateway is deployed separately. That separation keeps provider switching (YandexART, GigaChat, OpenAI, Runway, self-hosted models) out of the bot's domain core and lets each country/deployment use its own provider policy without branching Metrotherapy business logic.

## Staff commands

- `/creative_image <concept>`
- `/creative_video <concept>`
- `/creative_status <job_id>`

The commands are available only while the capability is enabled, and then only to superadmins or `admin`/`marketing` staff whose existing scoped-permission policy permits `admin:visual:creative`. The permission is also exposed in the existing admin permission list.

Each request is scoped to `staff:<telegram-user-id>`. Idempotency uses the staff id, Telegram chat id, message id, and visual kind. The chat id is required because Telegram message ids are not globally unique.

The client treats the gateway as a separate trust boundary. A generation or polling response is accepted only when its returned `scope_id` exactly matches the requested scope; generation responses must also preserve the requested visual kind. Gateway-controlled provider/model/error metadata is syntax-bounded before it can reach Telegram output.

## Runtime configuration

See `deploy/visual-creative.env.example`. New deployments should set `VISUAL_CREATIVE_ENABLED` explicitly. When it is absent, an already configured gateway URL/token still activates the capability for backward compatibility with deployments created before the flag existed.

When the capability is enabled, configuration readiness requires:

- a valid `VISUAL_GATEWAY_URL`;
- a non-empty `VISUAL_GATEWAY_TOKEN`;
- a two-letter `VISUAL_DEPLOYMENT_COUNTRY`;
- secure HTTPS transport in production.

An explicitly disabled capability does not gate Metrotherapy readiness. An enabled but incomplete/unsafe configuration makes `/readyz` return not-ready, while `/healthz` remains a liveness/diagnostic probe. The readiness check is configuration-only: it deliberately does not make an outbound network request to the external gateway, so a transient creative-provider outage cannot take the therapeutic core out of service.

Production startup applies the same configuration contract through `services/validators/visual_creative.py` from the normal `validate_all()` path. This makes an enabled but broken Visual Creative configuration fail fast before the worker starts serving traffic instead of relying only on a later readiness probe. Disabled Visual Creative remains completely optional and does not block startup.

Do not commit real gateway tokens. The gateway URL parser rejects embedded credentials, query strings, fragments, malformed ports, traversal-style prefixes, control characters, and unsupported schemes. Non-loopback `http://` endpoints fail closed by default; `VISUAL_GATEWAY_ALLOW_INSECURE_HTTP=1` is an explicit escape hatch for controlled development networks but is not considered production-ready. Loopback HTTP remains available for local development.

Outbound gateway requests do not follow HTTP redirects. This prevents the configured bearer credential and request payload from being forwarded to a different origin through a 3xx response. JSON responses must identify themselves as JSON when a `Content-Type` header is present.

## Media lifecycle

Generated media remains durable at the gateway. Metrotherapy streams only a bounded temporary local copy for Telegram upload instead of buffering the complete image/video in process memory. The stream is written to a unique temporary file, atomically materialized only after the byte limit and MIME checks pass, and deleted after the Telegram upload attempt. Partial files are removed on failed/oversized downloads. This prevents generated media from accumulating indefinitely in the bot runtime directory and bounds memory pressure from large video responses.

## Provider independence

The Metrotherapy client does not select a concrete provider by default. It sends the deployment country and the creative brief to the gateway. Provider routing, credentials, quotas, idempotency reservations, retry/failover policy, and model configuration remain gateway responsibilities.

The standalone gateway supplied with the integration archive must therefore be deployed as its own service/repository rather than copied into the Metrotherapy monolith.

## Validation contract

The integration must pass the repository's existing CI without lowering or bypassing any gate. In particular, existing regression, coverage/branch-coverage ratchets, Ruff runtime-danger checks, critical mypy/Bandit checks, dependency audit, and PostgreSQL payment/concurrency probes remain authoritative.

Focused tests cover capability enable/disable/legacy activation, configuration readiness, production startup fail-fast, gateway request scoping, exact response scope/kind matching, response/media bounds, streaming cleanup, MIME and JSON content-type validation, fail-closed URL parsing, HTTPS-by-default transport, redirect rejection, IPv6 URL rendering, staff authorization, cross-chat idempotency, readiness integration, router registration, and temporary-file cleanup.
