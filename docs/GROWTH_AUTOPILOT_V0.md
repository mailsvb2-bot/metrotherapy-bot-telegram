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
- show read-only creative diagnostics in the Growth report.

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

## Evidence sources

v0 reads existing project data only:

- `events` for redirect clicks, `/start`, demo, tariff, payment-related events;
- `demo_events` for demo sent/ack counters;
- `payments` for paid count, distinct paying users, and revenue;
- `admin_ad_links` for source/campaign/creative/ad_spend coverage;
- `payments` + `subscriptions` for paid-without-access risks;
- `services.segments.segment_counts()` when available;
- `services.funnel2_analytics.scenario_counts()` when available.

All period reports keep evidence in the selected period. For example, `today` must not show old ad links, old spend, old clicks, or old paid-without-access alerts as if they happened today.

Payment rows and paying users are deliberately separate metrics:

- `payments` counts successful payment rows;
- `paid_users` counts distinct users who paid in the period;
- user-based conversion rates and scale recommendations use `paid_users`, not payment rows.

All reads are defensive. Missing optional tables must produce degraded evidence, not a broken admin panel.

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
5. Guarded apply gateway with budget limits and kill-switch.
6. Platform adapters: Yandex Direct, VK Ads, TgAds manual/import, MAX manual/channel tracking.
7. Autopilot limited mode only after evidence, guardrails, tests, and rollback are production-ready.
