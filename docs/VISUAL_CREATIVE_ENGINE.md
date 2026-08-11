# Visual Creative Engine

Metrotherapy uses Visual Creative Engine as optional staff/marketing tooling. It is deliberately outside the therapeutic decision core: visual generation, render packs and marketing experiments cannot change therapy selection, diagnosis, user state, payments, access, scheduling or any other therapeutic/user-facing business decision.

## Architecture

The bot keeps product decisions and provider execution separated:

1. `services/visual_creative_gateway.py` — the existing bounded HTTP client and trust boundary for image/video generation.
2. `services/visual_creative_render_gateway.py` — additive render-pack client that reuses the same hardened transport primitives without rewriting the already-tested generation client.
3. `services/visual_creative_capability.py` — base capability and separately staged Studio/readiness contract.
4. `services/metrotherapy_visual_creatives.py` — existing Metrotherapy-specific single-creative marketing brief and safety/brand policy.
5. `services/metrotherapy_creative_experiments.py` — observed marketing evidence only; it ranks leaders and reports a statistical winner only when the observed rate evidence supports that claim.
6. `services/metrotherapy_creative_studio.py` — deterministic Metrotherapy Brand DNA, three local creative directions, preflight checks, stable variant identity and render-pack specification.
7. `handlers/admin_visual_creatives.py` — staff authorization, Telegram commands and temporary media delivery.

The provider gateway is deployed separately. That separation keeps provider switching (for example YandexART, GigaChat, OpenAI, Runway or self-hosted models) out of the bot's domain core and lets each country/deployment use its own provider policy without branching Metrotherapy business logic. The gateway is an execution boundary, not a second Metrotherapy decision engine.

## Staff commands

Existing commands remain unchanged:

- `/creative_image <concept>`
- `/creative_video <concept>`
- `/creative_status <job_id>`

Creative Studio adds:

- `/creative_concepts <idea>` — creates three deterministic local directions without a paid provider call.
- `/creative_pack image|video 1|2|3 <idea>` — generates only the selected direction and, once the source asset is ready, requests a deterministic platform render pack.

All commands remain staff-only. The base commands require enabled Visual Creative and the existing `admin:visual:creative` permission for `admin`/`marketing` staff (superadmins retain their existing override). Studio commands require the same authorization **and** `VISUAL_CREATIVE_STUDIO_ENABLED=1`.

Each request is scoped to `staff:<telegram-user-id>`. The legacy single-creative commands keep their chat/message-scoped idempotency. Studio paid generation/render idempotency is bound to a stable variant identity computed from the complete normalized effective creative specification: experiment/country, art direction, provider brief, formats, composition copy and brand colors. A changed CTA, format set or brand presentation therefore becomes a different realization rather than accidentally reusing stale paid work.

## Runtime configuration and staged rollout

See `deploy/visual-creative.env.example`.

`VISUAL_CREATIVE_ENABLED` controls the existing base capability. When it is absent, an already configured gateway URL/token still activates the base capability for backward compatibility with deployments created before the flag existed.

`VISUAL_CREATIVE_STUDIO_ENABLED` is intentionally different: it is explicit-only and defaults to disabled. Enable it only after the separately deployed gateway supports the reviewed v5.1+ render-pack API. This protects an existing v4 gateway deployment from suddenly exposing Studio commands that it cannot execute. Enabling Studio while the base capability is disabled fails readiness/startup validation.

When the base capability is enabled, configuration readiness requires:

- a valid `VISUAL_GATEWAY_URL`;
- a non-empty `VISUAL_GATEWAY_TOKEN`;
- a two-letter `VISUAL_DEPLOYMENT_COUNTRY`;
- secure HTTPS transport in production.

An explicitly disabled base capability remains readiness-neutral. An enabled but incomplete/unsafe configuration makes `/readyz` return not-ready, while `/healthz` remains a liveness/diagnostic probe. The readiness check is configuration-only: it deliberately does not make an outbound request to the external gateway, so a transient creative-provider outage cannot take the therapeutic core out of service. Production startup applies the same contract through `services/validators/visual_creative.py` from the normal `validate_all()` path.

The Studio enable flag is an operator rollout assertion, not a network capability probe. Production deployment must upgrade the separate gateway to v5.1+ first and only then set `VISUAL_CREATIVE_STUDIO_ENABLED=1`.

Do not commit real gateway tokens. The gateway URL parser rejects embedded credentials, query strings, fragments, malformed ports, traversal-style prefixes, control characters and unsupported schemes. Non-loopback `http://` endpoints fail closed by default; `VISUAL_GATEWAY_ALLOW_INSECURE_HTTP=1` is a controlled-development escape hatch and is not production-ready. Outbound requests do not follow redirects, preventing bearer credentials/payloads from being forwarded to another origin.

## Creative Studio and marketing evidence

The Studio prepares three deterministic Metrotherapy directions locally before any paid AI call. It uses Brand DNA plus a deterministic composition contract and instructs image/video generation to leave safe copy space instead of trusting model-generated pixels for essential readable promotional typography.

Preflight blocks explicit cure/guarantee/coercion patterns before the paid provider call. This is a marketing-content guardrail only; it does not diagnose users or infer treatment efficacy.

`services/metrotherapy_creative_experiments.py` consumes observed marketing metrics only. It does not impose a fictional universal click/open/purchase funnel across channels: each observed event count is bounded by the supplied impression/exposure denominator. For rate objectives it distinguishes an observed `leader` from a statistically supported `winner`; a winner requires a two-sided 95% comparison with Bonferroni adjustment across competitors. Cost-per-purchase remains an observed cost leader and never receives a significance label from this model.

## Render-pack trust boundary

Supported formats are fixed by the client contract:

- `square` — 1080×1080
- `feed` — 1080×1350
- `story` — 1080×1920
- `landscape` — 1200×628

The client does not trust a successful gateway response merely because it is HTTP 200. A render pack is accepted only when its pack id, exact staff scope, source job, status/error metadata, requested format set, source kind, published dimensions, media MIME and SHA-256 metadata are valid. A succeeded pack must contain exactly the requested formats and each asset must be ready with a complete digest.

Render media is streamed with the same bounded transport policy as generation media. The response MIME must match the trusted asset metadata (with `application/octet-stream` treated only as transport fallback), the file is written to a unique temporary path, SHA-256 is verified before atomic materialization, and the Telegram-side copy is removed after upload. Partial/oversized/mismatched files are deleted.

## Provider independence

Metrotherapy sends the deployment country and creative brief to the gateway but does not hard-code a concrete provider. Provider routing, credentials, quotas, model configuration, generation idempotency reservations, render reservations, retry/crash-recovery policy and deterministic composition implementation remain gateway responsibilities.

The standalone gateway patch supplied with the reviewed v5.1 archive belongs to the separate gateway service/repository and must **not** be copied into the Metrotherapy monolith.

## Validation contract

This integration must pass the repository's existing CI without lowering, bypassing or broadening any gate. Existing regression tests, total/branch coverage ratchets, Ruff runtime-danger checks, critical mypy/Bandit checks, dependency audit, PostgreSQL payment/concurrency probes, release gates and user-scenario checks remain authoritative.

Focused regressions cover base-capability backward compatibility, explicit Studio staging, full creative-spec variant identity, country binding, pre-provider safety, observed marketing evidence, exact render scope/source/format/kind/dimension/MIME/digest validation, bounded streamed media, staff authorization and temporary-file cleanup. The full repository CI remains the final acceptance gate.
