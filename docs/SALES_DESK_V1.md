# Sales Desk v1

Sales Desk is the operational sales workspace inside the Telegram admin panel.
It is deliberately isolated from the customer runtime, payment reconciliation,
practice-token accounting, Growth Apply and messenger delivery.

## Admin workflow

1. Open `Админка -> Sales Desk`.
2. Filter the queue by open, overdue, mine, unassigned or a specific stage.
3. Open a lead card.
4. Claim the lead, move it through an allowed stage, schedule the next contact or add a note.
5. Review the immutable audit history from the card.

Stages:

- `new` — discovered from a relevant funnel event;
- `contacted` — a manager recorded contact;
- `qualified` — the user showed product or tariff interest;
- `checkout` — payment was started;
- `won` — a provider-confirmed successful payment exists;
- `lost` — manually closed without a sale.

Automatic source synchronization can advance an auto-managed lead and always
promotes a lead to `won` after verified payment evidence. It never silently
demotes a lead and does not overwrite a manually managed stage with weaker
analytics evidence.

## Storage

Sales Desk owns three tables:

- `sales_leads` — current operational projection;
- `sales_lead_notes` — manager notes;
- `sales_lead_audit` — append-only action history.

The projection is keyed by a stable user lead key. Telegram/admin identifiers
are stored as `BIGINT` for PostgreSQL safety.

## Permissions

- `admin:sales` — read Sales Desk;
- `admin:sales:write` — claim leads, change stage, schedule follow-up and add notes.

The write permission is fail-closed. Legacy `allowed_perms=None` preserves
read-only navigation but does not grant write access. A super-admin has both.

## Safety contract

Sales Desk does not:

- send messages to customers;
- alter prices, tariffs, subscriptions or practice-token balances;
- edit payment rows or provider reconciliation state;
- execute Growth Apply actions;
- change Telegram, MAX or VK delivery behavior;
- modify the public user funnel.

A manager action affects only Sales Desk-owned tables. Source synchronization
reads existing events, user display data and verified successful payments.
