# Growth Autopilot v0

Growth Autopilot v0 is the first safe layer of the autonomous advertising-sales loop for Metrotherapy.

## Current scope

v0 is intentionally **read-only / plan-only**.

It can:

- read bot analytics;
- read ad tracking links;
- read redirect click events;
- read source/campaign/creative attribution;
- read demo and payment evidence;
- find gaps in attribution/spend data;
- detect payment/access risks;
- produce a daily action plan with evidence;
- show recommendations in the Telegram admin panel;
- show a read-only Action Inbox with recommendation cards and evidence;
- show read-only creative diagnostics in the Growth report;
- store provider-verified payment conversions in a dry-run outbox;
- bridge canonical demo/tariff events into the same dry-run outbox.

It must not:

- change ad budgets;
- call external advertising write APIs;
- send conversion postbacks;
- change tariffs;
- change user-facing funnel logic;
- send marketing messages automatically;
- issue direct actions without human review.

## Admin entry point

Telegram admin panel:

```text
🤖 Growth Autopilot
```

Callbacks:

```text
admin:growth:autopilot
admin:growth:autopilot:today
admin:growth:autopilot:week
admin:growth:autopilot:month
admin:growth:autopilot:all
admin:growth:autopilot:report:<period>
admin:growth:autopilot:inbox:<period>
admin:growth:autopilot:action:ga:<index>:<period>
admin:growth:autopilot:conversions:<period>
```

## Redirect click tracking

If `GROWTH_CLICK_BASE_URL` or `METRO_GROWTH_CLICK_BASE_URL` or `PUBLIC_BASE_URL` is configured, newly created ad links also show a tracking URL:

```text
https://<public-base>/a/<payload>
```

The health/runtime aiohttp server handles:

```text
GET /a/{payload}
```

The route records a best-effort `ad_click_redirect` event and then returns a `302` redirect to the Telegram start URL:

```text
https://t.me/<bot>?start=<payload>
```

Safety rules:

- if event logging fails, the redirect still happens;
- no IP address is stored by this feature;
- only sanitized payload, attribution fields, user agent, and referer are recorded;
- Telegram polling/webhook behavior is not changed;
- direct Telegram links remain available as fallback.

## Creative diagnostics

Creative diagnostics are read-only and derived from existing evidence:

- `admin_ad_links.latest` gives source/campaign/creative/spend labels;
- `events.meta` or `events.payload` gives attribution for redirect clicks, `/start`, demo, tariff and payment events;
- missing attribution is counted as `unattributed_events` rather than guessed.

The report groups by:

```text
source / campaign / creative
```

For each group it can show:

- links;
- low-confidence spend parsed from manual labels;
- redirect clicks;
- `/start`;
- demo acknowledgements;
- tariff opens;
- payments;
- click→start;
- demo_ack→payment;
- estimated CPC/CPP when spend is available.

This is diagnostic only. It does not create, edit, pause, or scale ad creatives.

## Conversion Hub dry-run

Conversion Hub introduces a typed outbox without an outbound sender.

Table:

```text
growth_conversion_outbox
```

Provider payment ingestion remains deliberately narrow:

- only the public provider-verified YooKassa wrapper is connected;
- only reconciled successful payments with completed side effects are accepted;
- payment problems, failed grants, pending statuses and provider-verification failures are not conversions;
- gift payments become `gift_paid`; regular payments become `payment_success`.

Supported domain types:

```text
demo_ack
tariff_open
payment_success
gift_paid
```

Every row is hard-locked to:

```text
mode = dry_run
status = planned
dispatch_allowed = 0
attempts = 0
```

There is intentionally no sender or flush function in this stage.

### Idempotency

When a provider event ID exists, it is the stable business identity together with:

```text
conversion_type + source_platform + external_event_id
```

Webhook retries with corrected metadata or amount must resolve to the same idempotency key rather than create duplicates. Payload/user/amount are used only as fallback identity for sources without an external event ID.

### Failure isolation

Growth conversion ingestion is best-effort and happens only after provider verification and payment reconciliation. A missing migration, DB error or malformed Growth payload must not:

- roll back a payment;
- revoke or block access;
- change payment reconciliation status;
- break the public webhook response.

The Conversion Hub table is intentionally not added to the core P0 readiness-table set. The migration is applied through the normal deterministic migration chain, but Growth degradation must not stop the primary payment and delivery runtime.

## Event-to-conversion bridge

The bridge converts canonical internal events into dry-run Conversion Hub rows without adding calls to individual handlers.

State table:

```text
growth_conversion_bridge_state
```

Current mappings:

```text
demo_ack      -> demo_ack
sub_menu_open -> tariff_open
```

`sub_menu_open` is used as the canonical tariff-open event. Merely showing a tariffs command or button is not treated as an opened tariff screen.

### Cursor and atomicity

The bridge reads:

```text
events.id > last_event_id
```

in ascending batches. For every event it uses:

```text
external_event_id = events:<id>
```

Outbox inserts and cursor advancement happen in one transaction. If any event in a batch fails:

- all outbox inserts from that batch roll back;
- the cursor does not advance;
- the same events remain available for a later retry.

### Attribution at event time

For each downstream event the bridge reads the latest preceding `funnel_start_command` for the same user with:

```text
start_event.id <= downstream_event.id
```

This prevents a later campaign visit from being incorrectly attached to an earlier demo or tariff event. Missing attribution stays empty; the bridge does not guess a source.

### Scheduler ownership

The canonical scheduler runs the bridge through a protected, configurable tick:

```text
GROWTH_CONVERSION_BRIDGE_INTERVAL_SEC
GROWTH_CONVERSION_BRIDGE_BATCH_SIZE
GROWTH_CONVERSION_BRIDGE_TIMEOUT_SEC
```

Expected Growth schema/storage failures are returned as a degraded bridge result and do not mark the primary scheduler as failed. Unexpected programming failures still pass through the existing scheduler protected-tick diagnostics.

The Conversion Hub admin screen shows:

- cursor event ID;
- last batch size;
- inserted rows;
- duplicates;
- update timestamp;
- degraded error when the optional schema is unavailable.

## Evidence sources

v0 reads existing project data only:

- `events` for redirect clicks, `/start`, demo, tariff, payment-related events;
- `demo_events` for demo sent/ack counters;
- `payments` for paid count, distinct paying users, and revenue;
- `admin_ad_links` for source/campaign/creative/ad_spend coverage;
- `growth_conversion_outbox` for dry-run conversion plans;
- `growth_conversion_bridge_state` for bridge cursor/diagnostics;
- `payments` + `subscriptions` for paid-without-access risks;
- `services.segments.segment_counts()` when available;
- `services.funnel2_analytics.scenario_counts()` when available.

All period reports keep evidence in the selected period. For example, `today` must not show old ad links, old spend, old clicks, old conversions, or old paid-without-access alerts as if they happened today.

Payment rows and paying users are deliberately separate metrics:

- `payments` counts successful payment rows;
- `paid_users` counts distinct users who paid in the period;
- user-based conversion rates and scale recommendations use `paid_users`, not payment rows.

All reads are defensive. Missing optional tables must produce degraded evidence, not a broken primary runtime.

## Action Inbox safety

Action Inbox cards are derived from recommendations. They are **not** executable actions.

Each card keeps the v0 safety contract:

- `apply_mode="manual_review_required"`;
- `autopilot_can_apply_now=False`;
- no budget mutation;
- no ad-cabinet API write;
- no conversion postback;
- no tariff/funnel mutation;
- no automatic marketing send.

The stable callback `admin:growth:autopilot:action:ga:<index>:<period>` opens a card by position in the current period snapshot. This is intentionally read-only and recalculates evidence from current data.

## Safety contract

Every v0 recommendation includes:

- `priority`;
- `kind`;
- `evidence`;
- `recommended_action`;
- `confidence`;
- `risk`;
- `apply_mode="manual_review_required"`;
- `autopilot_can_apply_now=False`.

This is a regression lock. Future versions must not silently weaken it.

## Next stages

1. Action Inbox with explicit admin confirmation.
2. Redirect click tracking: `click -> /start`.
3. Creative library and creative diagnostics.
4. Conversion Hub / postback queue in dry-run mode.
5. Unified event-to-conversion bridge for demo/tariff events.
6. Guarded apply gateway with budget limits and kill-switch.
7. Platform adapters: Yandex Direct, VK Ads, TgAds manual/import, MAX manual/channel tracking.
8. Autopilot limited mode only after evidence, guardrails, tests, and rollback are production-ready.
