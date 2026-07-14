# Sales Desk v1

Sales Desk is the operational sales workspace inside the Telegram admin panel.
It is deliberately isolated from payment reconciliation, practice-token
accounting, Growth Apply and the public customer funnel.

## Admin workflow

1. Open `Админка -> Sales Desk`.
2. Filter the queue by open, overdue, mine, unassigned or a specific stage.
3. Open a lead card.
4. Claim the lead, move it through an allowed stage, schedule the next contact,
   add a note or send a manual Telegram message.
5. Review the immutable audit history from the card.

Stages:

- `new` — discovered from a relevant funnel event;
- `contacted` — a manager recorded or successfully sent a contact;
- `qualified` — the user showed product or tariff interest;
- `checkout` — payment was started;
- `won` — a provider-confirmed successful payment exists;
- `lost` — manually closed without a sale.

Automatic source synchronization can advance an auto-managed lead and always
promotes a lead to `won` after verified payment evidence. It never silently
demotes a lead and does not overwrite a manually managed stage with weaker
analytics evidence. A `won` lead is terminal so historical payment evidence
cannot reopen it.

## Storage

Sales Desk owns four tables:

- `sales_leads` — current operational projection;
- `sales_lead_notes` — manager notes;
- `sales_lead_audit` — append-only action history;
- `sales_outbound_messages` — prepared and finalized manual contact attempts.

The projection is keyed by a stable user lead key. Telegram/account/admin
identifiers are stored as `BIGINT` for PostgreSQL safety.

Outbound statuses are:

- `prepared` — durable row exists before the Telegram call;
- `sent` — Telegram returned a provider message identifier;
- `failed` — Telegram returned a definitive rejection;
- `uncertain` — a timeout or network failure made the delivery outcome unknown.

An uncertain message is not automatically retried. This prevents accidental
duplicate contact after an ambiguous network result.

## Permissions

- `admin:sales` — read Sales Desk;
- `admin:sales:write` — claim leads, change stage, schedule follow-up and add
  notes;
- `admin:sales:message` — send an individual manual Telegram message from an
  assigned lead card.

Write and message permissions are fail-closed. Legacy `allowed_perms=None`
preserves read-only navigation but grants neither operational changes nor
outbound messages. A super-admin has all three permissions.

## Messaging contract

Messaging is always initiated by a manager from one lead card. There is no
scheduler, campaign dispatcher, bulk-send path or automatic retry.

Before sending, Sales Desk atomically:

1. verifies a valid Telegram identity;
2. verifies or assigns ownership;
3. writes a `prepared` outbox row;
4. appends an audit event.

After the Telegram call, the row is finalized as `sent`, `failed` or
`uncertain`. A successful first contact moves `new` to `contacted`. The message
text is stored in the Sales Desk outbox, while the general audit records only
technical delivery metadata.

## Safety contract

Sales Desk does not:

- send automated or bulk marketing messages;
- alter prices, tariffs, subscriptions or practice-token balances;
- edit payment rows or provider reconciliation state;
- execute Growth Apply actions;
- change Telegram, MAX or VK customer delivery behavior;
- modify the public user funnel.

Operational writes affect only Sales Desk-owned tables. Source synchronization
reads existing events, user display data and verified successful payments.
