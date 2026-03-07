# HubSpot Property Reference

Single source of truth for every property the sync system reads, writes, or depends on.

---

## Company Properties

### Sync Identity

#### `platform_org_id`

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Set by** | Organization Sync (linker, company creator) |
| **Updated by** | Never overwritten once set |
| **Source** | Platform DB `organizations.id` (UUID) |

Primary key that links a HubSpot company to a platform organization. Every other
sync operation depends on this property being present. The internal property name
is configurable via the `HUBSPOT_PLATFORM_ORG_ID_PROPERTY` environment variable
(default: `platform_org_id`).

- Set when a company is auto-linked (confidence >= 0.8) or when a placeholder
  company is created.
- The Analytics Sync uses this property to find all companies that need updating:
  it queries HubSpot for every company where `platform_organization_id` is set.

> **Note:** `platform_organization_id` is a separate property set by the Analytics
> Sync (see below). Both contain the same UUID but are written at different stages.

---

#### `platform_organization_id`

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `organizations.id` (UUID) |

Written by `to_hubspot_properties()` during every analytics sync run. Contains
the same value as `platform_org_id` but is part of the analytics property batch.

---

#### `standard_lab`

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Set by** | Company Creator (placeholder creation) |
| **Value** | `"true"` |

Flag indicating the company was created by the sync system (i.e. it originates
from the platform). Set once during placeholder company creation and never
changed.

---

#### `likely_spam`

| | |
|---|---|
| **HubSpot type** | Single-line text (boolean) |
| **Set by** | Company Creator |
| **Updated by** | Company Creator (enrichment pass) |
| **Values** | `"true"` / `"false"` |

Marks whether a placeholder company is suspected spam. Determined at creation
time by `is_likely_spam()` which checks:

- Disposable email domain (admin email)
- Spam username pattern (8+ hex chars or 10+ digit numeric)
- No real platform usage and no Paddle subscription

Automatically cleared to `"false"` when the organization later shows real usage
or gets an active Paddle subscription.

---

#### `company_source_property` (configurable)

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Default property name** | `platform_has_used_prodcut` |
| **Set by** | Company Creator |
| **Values** | `"auto_created"` / `"enriched_from_paddle"` |

Tracks how a placeholder company was created and whether it has been enriched.
The property name is configurable via the `COMPANY_SOURCE_PROPERTY` environment
variable.

- `auto_created` -- placeholder company, no billing data yet.
- `enriched_from_paddle` -- placeholder was enriched with Paddle customer data.

The enrichment pass only modifies companies whose source is `auto_created`;
manually created or already-enriched companies are left untouched.

> **Note:** The default property name `platform_has_used_prodcut` is a legacy
> naming artifact. The Analytics Sync also writes a boolean to this same
> property name (see below), so the value will be overwritten to `"true"` /
> `"false"` once analytics sync runs for this company.

---

### Billing & Lifecycle

#### `platform_billing_active`

| | |
|---|---|
| **HubSpot type** | Single-line text (enumeration) |
| **Set by** | Analytics Sync |
| **Source** | Paddle API -- subscription status |
| **Values** | `"not started"` / `"active"` / `"cancelled"` |

Billing lifecycle of the organization's Paddle subscription.

| Value | Paddle subscription status |
|---|---|
| `not started` | No subscription found |
| `active` | `active`, `trialing`, or `past_due` |
| `cancelled` | `canceled` or `paused` |

**Suggested HubSpot workflows:**

- `active` --> set `company_status` = "Customer"
- `cancelled` --> set `company_status` = "Churned"

> **Note:** For `not started`, use `platform_testing_status` to distinguish between
> "Account Created" and "Testing". See `platform_testing_status` below.

---

#### `platform_testing_status`

| | |
|---|---|
| **HubSpot type** | Single-line text (enumeration) |
| **Set by** | Analytics Sync |
| **Source** | Derived from Paddle + Platform DB + usage |
| **Values** | `"account_created"` / `"testing"` / `"not_testing"` |

Testing/trial status of the organization.

```
account_created = (no active subscription AND no subscription history AND has NOT used product)
                  OR (NO_BILLING scope AND has NOT used product)
                  → Fresh signup, hasn't uploaded anything yet
testing         = (no active subscription AND no subscription history AND HAS used product)
                  OR (NO_BILLING scope AND HAS used product)
                  → Actively testing, has uploaded/processed something
not_testing     = has (or had) a Paddle subscription
                  → Paying or churned customer
```

When `"testing"`, the testing-specific properties (credits remaining, testing services,
success/failure counts) are included in the analytics sync. When `"account_created"` or
`"not_testing"`, those properties are not updated (they retain whatever value was last written).

**Suggested HubSpot workflows:**

- `account_created` → set `company_status` = "Account Created"
- `testing` → set `company_status` = "Testing"

---

#### `platform_has_account`

| | |
|---|---|
| **HubSpot type** | Single-line text (boolean) |
| **Set by** | Analytics Sync |
| **Source** | Computed |
| **Values** | `"true"` / `"false"` |

Always `"true"` if the organization exists in the platform database. Useful in
HubSpot to distinguish companies that have a platform account from companies that
are purely CRM records.

---

### Account Info

#### `platform_admin_email`

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `users.email` WHERE `organizations.admin_user_id` |

Email address of the organization's administrator. Empty string if no admin is
set.

---

#### `platform_organisation_accounts`

| | |
|---|---|
| **HubSpot type** | Number (stored as string) |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `COUNT(*) FROM users WHERE organization_id = ?` |

Total number of user accounts in the organization.

---

#### `platform_signed_up_date`

| | |
|---|---|
| **HubSpot type** | Date |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `MIN(date) FROM usage_transactions WHERE type = 'GIFT_TOPUP'` |
| **Format** | `YYYY-MM-DD` |

Date of the organization's first `GIFT_TOPUP` transaction (welcome bonus),
which corresponds to their signup date. Only included in the update if a value
exists.

**Suggested HubSpot workflow:** Use for time-based onboarding sequences.

---

#### `platform_last_usage_date`

| | |
|---|---|
| **HubSpot type** | Date |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `MAX(date) FROM usage_transactions` |
| **Format** | `YYYY-MM-DD` |

Most recent transaction date across all transaction types. Only included in the
update if a value exists.

**Suggested HubSpot workflows:**

- More than 7 days ago AND `platform_billing_active` = `not started` --> stalled trial, trigger re-engagement
- Unknown AND `platform_billing_active` = `not started` --> never started, trigger onboarding

---

### Usage Metrics

#### `platform_has_used_prodcut`

| | |
|---|---|
| **HubSpot type** | Single-line text (boolean) |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `EXISTS(SELECT 1 FROM usage_transactions WHERE type = 'ORDER_USAGE')` |
| **Values** | `"true"` / `"false"` |

Whether the organization has ever made a real product usage transaction (not just
the welcome bonus).

> **Note:** The property name contains a typo (`prodcut` instead of `product`).
> This is intentional to match the existing HubSpot property.

---

#### `platform_usage_last_7_days`

| | |
|---|---|
| **HubSpot type** | Number (stored as string) |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `SUM(ABS(amount)) FROM usage_transactions WHERE type = 'ORDER_USAGE' AND date >= NOW() - 7 days` |

Total credits consumed in the last 7 days.

---

#### `platform_usage_last_30_days`

| | |
|---|---|
| **HubSpot type** | Number (stored as string) |
| **Set by** | Analytics Sync |
| **Source** | Platform DB same query with 30-day window |

Total credits consumed in the last 30 days.

---

#### `platform_usage_trend`

| | |
|---|---|
| **HubSpot type** | Single-line text (enumeration) |
| **Set by** | Analytics Sync |
| **Source** | Derived |
| **Values** | `"up"` / `"stable"` / `"down"` |

Compares the last 30 days of usage against the previous 30 days (days 31-60).

```python
change_percent = ((current_30d - previous_30d) / previous_30d) * 100
if change_percent > 10:  "up"
if change_percent < -10: "down"
else:                     "stable"
```

**Suggested HubSpot workflow:** `up` AND `platform_billing_active` = `not started` --> flag as hot lead.

---

#### `platform_services_used`

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `orders` JOIN `services` (last 30 days) |
| **Format** | `"Service A (45), Service B (12)"` |

Comma-separated list of services used in the last 30 days, sorted by usage count
descending.

---

### Testing Properties (conditional)

These properties are **only written when `platform_testing_status` = `"testing"`** (i.e.
the org has actually used the product but has no subscription). For `"account_created"`,
paying, or churned customers they are not updated and retain their last value.

#### `platform_free_credits_remaining`

| | |
|---|---|
| **HubSpot type** | Number (stored as string) |
| **Set by** | Analytics Sync (when `testing_status = "testing"`) |
| **Source** | Platform DB `-1 * organizations.usage` |

Remaining free credits. The database stores usage as a positive number; the sync
negates it so a positive value = credits remaining, negative = overdrawn.

**Suggested HubSpot workflow:** Value < 100 AND `platform_billing_active` = `not started` --> trigger sales outreach.

---

#### `platform_testing_services_used`

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Set by** | Analytics Sync (when `testing_status = "testing"`) |
| **Source** | Platform DB `orders` JOIN `services` (all time) |
| **Format** | `"Service A (120), Service B (45)"` |

All services the organization has ever used during the testing period, with
counts. Unlike `platform_services_used` (30-day window), this covers the entire
trial period.

---

#### `platform_testing_succesful_cases`

| | |
|---|---|
| **HubSpot type** | Number (stored as string) |
| **Set by** | Analytics Sync (when `testing_status = "testing"`) |
| **Source** | Platform DB `COUNT(*) FROM jobs WHERE job_status = 'Done'` |

Total successful job completions, all time.

> **Note:** Property name contains a typo (`succesful` instead of `successful`).
> This is intentional to match the existing HubSpot property.

---

#### `platform_testing_failed_cases`

| | |
|---|---|
| **HubSpot type** | Number (stored as string) |
| **Set by** | Analytics Sync (when `testing_status = "testing"`) |
| **Source** | Platform DB `COUNT(*) FROM jobs WHERE job_status = 'Failed'` |

Total failed jobs, all time.

---

### Issues

#### `platform_number_errors_last_30_days`

| | |
|---|---|
| **HubSpot type** | Number (stored as string) |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `COUNT(*) FROM jobs WHERE job_status = 'Failed' AND timestamp >= NOW() - 30 days` |

Failed jobs in the last 30 days. Use for proactive support outreach.

---

#### `platform_refunds_last_30_days`

| | |
|---|---|
| **HubSpot type** | Number (stored as string) |
| **Set by** | Analytics Sync |
| **Source** | Platform DB `COUNT(*) FROM feedback WHERE created_at >= NOW() - 30 days` |

Customer feedback/refund requests in the last 30 days. Use for
satisfaction/churn risk alerts.

---

### Billing Address (Paddle Enrichment)

These standard HubSpot properties are filled from Paddle billing data **only
when the existing value is empty**. Once set (manually or via enrichment), they
are never overwritten -- HubSpot remains the source of truth.

| Property | Paddle source | Description |
|---|---|---|
| `name` | Customer / business name | Company display name |
| `domain` | _(from admin email)_ | Set at placeholder creation if email domain is not generic |
| `country` | Address `country_code` | ISO 3166-1 alpha-2 (e.g. `US`, `GB`) |
| `city` | Address `city` | City |
| `state` | Address `region` | State, county, or region |
| `zip` | Address `postal_code` | ZIP / postal code |
| `vat_number` | Business `tax_identifier` | VAT / tax ID |

**Paddle API endpoints used:**

| Endpoint | Data |
|---|---|
| `GET /customers/{id}` | Name, email |
| `GET /businesses?customer_id={id}` | Business name, tax identifier |
| `GET /customers/{id}/addresses` | Country, city, region, postal code |

---

## Contact Properties

Contacts are created from platform users and associated with the linked company.
The sync system only sets a minimal set of properties at creation time. It does
**not** update contacts after initial creation.

### `email`

| | |
|---|---|
| **HubSpot type** | Email |
| **Set by** | Contact Sync (creation only) |
| **Source** | Platform DB `users.email` |

Primary identifier. Used to search for existing contacts before creating.

---

### `firstname`

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Set by** | Contact Sync (creation only) |
| **Source** | Platform DB `users.first_name` |

---

### `lastname`

| | |
|---|---|
| **HubSpot type** | Single-line text |
| **Set by** | Contact Sync (creation only) |
| **Source** | Platform DB `users.last_name` |

---

### Company Association

| | |
|---|---|
| **HubSpot type** | Association (Contact --> Company) |
| **Set by** | Contact Sync |

After a contact is created (or found by email), it is associated with the
company that the organization is linked to. If the association already exists,
it is skipped.

---

## Task Properties

Tasks are created by the Organization Sync when a match requires human review.
They are **never** created by the Analytics Sync.

### `hs_task_subject`

Contains a tag `[ORG:{org_id}]` for duplicate detection.

| Scenario | Subject format |
|---|---|
| **Conflict** | `[ORG:{id}] Link conflict: {email} -> {company} (already linked to another org)` |
| **Multiple matches** (with placeholder) | `[ORG:{id}] Verify placeholder for {email} -- merge with {candidates}?` |
| **Multiple matches** (no placeholder) | `[ORG:{id}] Pick correct company for {email}: {candidates}?` |
| **Needs review** (with placeholder) | `[ORG:{id}] Verify placeholder for {email} -- possible match: {company} ({confidence}%)` |
| **Needs review** (no placeholder) | `[ORG:{id}] Verify match: {email} -> {company} ({confidence}% confidence)` |
| **No match** | `[ORG:{id}] No company found for {email} ({user_count} users)` |

### `hs_task_body`

Markdown-formatted body with sections: WHO (admin, users, Paddle ID), WHAT
HAPPENED, candidate details, and WHAT TO DO (actionable next steps).

### `hs_task_status`

Always `"NOT_STARTED"`.

### `hs_task_type`

Always `"TODO"`.

### Associations

- Company association (type 192) -- linked to all candidate companies and/or
  the placeholder company.
- Contact association (type 204) -- linked to associated contacts when available.

### Task Queue

If `HUBSPOT_TASK_QUEUE_ID` is configured, tasks are assigned to that queue for
team routing.

---

## Sync Workflows

### Organization Sync

**Entry point:** `python -m hubspot_sync.sync_organizations`
**Schedule:** Every 6 hours (GitHub Actions cron)
**Direction:** Platform DB --> HubSpot

Discovers new platform organizations and links them to HubSpot companies.

```
Platform DB (all organizations)
    |
    v
Filter (blacklisted orgs, internal orgs, spam detection)
    |
    v
Collect matching signals (domain, contacts, Paddle name, existing ID)
    |
    v
Score signals (weighted average, multi-signal boost, conflict penalty)
    |
    +-- ALREADY_LINKED (platform_org_id set) --> optionally enrich with Paddle
    +-- AUTO_LINK (confidence >= 0.8) ----------> link company, set platform_org_id, sync contacts
    +-- NEEDS_REVIEW (confidence 0.4-0.8) ------> create placeholder + review task
    +-- MULTIPLE_MATCHES (2+ strong candidates) -> create placeholder + resolution task
    +-- CONFLICT (different org already linked) -> create placeholder + conflict task
    +-- NO_MATCH (no candidates) ----------------> create placeholder (if enabled)
```

**Properties written:** `platform_org_id`, `name`, `domain`, `standard_lab`,
`likely_spam`, `company_source_property`, billing address fields (enrichment).
Contact properties: `email`, `firstname`, `lastname`, plus company association.

---

### Analytics Sync

**Entry point:** `python -m hubspot_sync.sync_analytics`
**Schedule:** Every 6 hours (GitHub Actions cron, combined entry point)
**Direction:** HubSpot --> Platform DB --> Paddle API --> HubSpot

Updates analytics for all companies that already have a `platform_organization_id`.

```
HubSpot: get all companies with platform_organization_id
    |
    v
For each company, compute from Platform DB:
    +-- Account metrics (admin email, user count, signup date)
    +-- Usage metrics (credits, trends, services, last usage date)
    +-- Order metrics (success/failure counts, services)
    |
    v
Fetch from Paddle API:
    +-- Billing status (subscription state)
    +-- Customer info (for billing address enrichment)
    |
    v
Update HubSpot company properties (batch)
    +-- Fill empty billing address fields from Paddle
    +-- Sync contacts (create missing, associate with company)
```

**Properties written:** All `platform_*` analytics properties listed above, plus
billing address fields if empty.

---

## Matching Signals

When the Organization Sync tries to find the right HubSpot company for a
platform organization, it collects these signals:

| Signal | Weight | Confidence | Description |
|---|---|---|---|
| `EXISTING_PLATFORM_ID` | 1.0 | 1.0 (ground truth) | Company already has this org's platform ID |
| `DOMAIN_MATCH` | 0.4 | 0.7 -- 0.85 | Admin/user email domain matches company domain |
| `CONTACT_ASSOCIATION` | 0.35 | 0.4 -- 0.8 | Existing HubSpot contacts match org users |
| `PADDLE_NAME_MATCH` | 0.25 | 0.5 -- 0.9 | Paddle company name is similar to HubSpot company name |
| `PADDLE_VAT_MATCH` | 0.3 | -- | Paddle VAT matches HubSpot (partially implemented) |

**Combined score:** Weighted average of all signals, with a +0.05 to +0.15 boost
when 2+ different signal types agree. Capped at 0.95. Multiplied by 0.3 if a
conflict is detected (company already linked to a different org).

---

## Filtering & Spam Detection

### Organization Filters

| Filter | Effect |
|---|---|
| Blacklisted org IDs (`BLACKLISTED_ORG_IDS`) | Skipped entirely |
| Internal orgs (all users from blacklisted email domains) | Skipped entirely |
| Orgs with no users | Skipped entirely |

### Contact Filters

| Filter | Effect |
|---|---|
| Blacklisted email domains | Contact not synced |
| Blacklisted email patterns (substring) | Contact not synced |
| Disposable email domains | Synced but flagged as likely spam |

### Spam Indicators

A company is flagged `likely_spam = "true"` when its admin email matches any of:

- Disposable email provider (e.g. `tempmail.com`, `guerrillamail.com`)
- Bot-like username (8+ hex characters or 10+ digit numeric string)
- No real platform usage AND no Paddle subscription

---

## Configuration Reference

| Environment Variable | Default | Description |
|---|---|---|
| `HUBSPOT_API_KEY` | _(required)_ | HubSpot private app access token |
| `DB_HOST` | _(required)_ | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | _(required)_ | Database name |
| `DB_USER` | _(required)_ | Database user |
| `DB_PASSWORD` | _(required)_ | Database password |
| `SSH_HOST` | _(optional)_ | SSH tunnel host |
| `SSH_USER` | _(optional)_ | SSH tunnel user |
| `SSH_KEY_PATH` | _(optional)_ | Path to SSH private key |
| `SSH_KEY_BASE64` | _(optional)_ | Base64-encoded SSH key |
| `PADDLE_API_KEY` | _(optional)_ | Paddle API key for billing data |
| `PADDLE_VENDOR_ID` | _(optional)_ | Paddle vendor ID |
| `SLACK_WEBHOOK_URL` | _(optional)_ | Slack webhook for sync reports |
| `HUBSPOT_PLATFORM_ORG_ID_PROPERTY` | `platform_org_id` | HubSpot property name for the org link |
| `COMPANY_SOURCE_PROPERTY` | `platform_has_used_prodcut` | Property tracking company source |
| `AUTO_LINK_CONFIDENCE_THRESHOLD` | `0.8` | Minimum confidence to auto-link |
| `AUTO_CREATE_COMPANIES` | `false` | Create placeholder companies for unmatched orgs |
| `HUBSPOT_TASK_QUEUE_ID` | _(optional)_ | Task queue ID for review tasks |
| `DRY_RUN` | `false` | Preview changes without writing to HubSpot |

---

## Suggested HubSpot Workflows

The sync system pushes raw data only. All lifecycle management and business
logic should be implemented as HubSpot workflows.

### Lifecycle Management

| Trigger | Action |
|---|---|
| `platform_billing_active` = `active` | Set `company_status` = "Customer" |
| `platform_billing_active` = `not started` | Set `company_status` = "Testing" |
| `platform_billing_active` = `cancelled` | Set `company_status` = "Churned" |

### Trial Engagement

| Trigger | Action |
|---|---|
| `platform_last_usage_date` > 7 days ago AND `platform_billing_active` = `not started` | Mark stalled, trigger re-engagement |
| `platform_last_usage_date` is empty AND `platform_billing_active` = `not started` | Mark not started, trigger onboarding |
| `platform_free_credits_remaining` < 100 AND `platform_billing_active` = `not started` | Trigger sales outreach |

### Growth Signals

| Trigger | Action |
|---|---|
| `platform_usage_trend` = `up` AND `platform_billing_active` = `not started` | Flag as hot lead |
| `platform_usage_trend` = `down` AND `platform_billing_active` = `active` | Churn risk alert |

### Support

| Trigger | Action |
|---|---|
| `platform_number_errors_last_30_days` > threshold | Proactive support outreach |
| `platform_refunds_last_30_days` > threshold | Satisfaction review |

---

## Database Schema Reference

### Key Tables

| Table | Purpose |
|---|---|
| `organizations` | Org info, `admin_user_id`, `usage` (credits), `scopes`, `paddle_id` |
| `users` | User accounts, `email`, `first_name`, `last_name`, `organization_id` |
| `orders` | Customer orders, `timestamp`, `service_id`, `user_id` |
| `jobs` | Processing jobs, `job_status` (`Done`/`Failed`/`Submitted`), `order_id` |
| `services` | Service definitions, `name` |
| `usage_transactions` | Credit transactions (`ORDER_USAGE`, `GIFT_TOPUP`, etc.), `organization_id` |
| `feedback` | Customer feedback / refund requests, `order_id`, `created_at` |

### Relationships

```
organizations
    +-- users (organization_id)
    |       +-- orders (user_id)
    |               +-- jobs (order_id)
    |               +-- feedback (order_id)
    +-- usage_transactions (organization_id)
```
