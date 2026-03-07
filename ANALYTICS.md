# Analytics Property Definitions

This document defines all analytics properties synced to HubSpot companies.

## Property Categories

| Category | Applies To | Description |
|----------|------------|-------------|
| **Core** | All companies | Basic account info, always updated |
| **Usage** | All companies | Platform usage metrics |
| **Testing** | `testing_status="testing"` only | Free trial specific metrics (only for orgs that have used the product) |
| **Issues** | All companies | Errors and refunds |

---

## Core Properties (Always Synced)

### `platform_organization_id`
- **Type:** String (UUID)
- **Source:** Platform DB → `organizations.id`
- **Description:** Primary key linking HubSpot company to platform organization

### `platform_admin_email`
- **Type:** String
- **Source:** Platform DB → `users.email` WHERE `organizations.admin_user_id`
- **Description:** Email of the organization administrator

### `platform_has_account`
- **Type:** Boolean ("true"/"false")
- **Source:** Computed
- **Description:** Always "true" if organization exists in platform

### `platform_organisation_accounts`
- **Type:** Number (as string)
- **Source:** Platform DB → `COUNT(*) FROM users WHERE organization_id = ?`
- **Description:** Number of users in the organization

### `platform_billing_active`
- **Type:** Enum ("not started", "active", "cancelled")
- **Source:** Paddle API → subscription status
- **Description:** Billing status of the organization's Paddle subscription
- **Logic:**
  ```
  not started = no subscription found
  active      = subscription.status IN ('active', 'trialing', 'past_due')
  cancelled   = subscription.status IN ('canceled', 'paused')
  ```

### `platform_testing_status`
- **Type:** Enum ("account_created"/"testing"/"not_testing")
- **Source:** Computed (from Paddle billing + product usage)
- **Description:** Testing/trial status of the organization
- **Logic:**
  ```
  account_created = (no subscription AND has NOT used product) OR (NO_BILLING scope AND has NOT used product)
  testing         = (no subscription AND HAS used product) OR (NO_BILLING scope AND HAS used product)
  not_testing     = has (or had) a Paddle subscription
  ```

### `platform_has_used_prodcut`
- **Type:** Boolean ("true"/"false")
- **Source:** Platform DB → `EXISTS(SELECT 1 FROM usage_transactions WHERE type = 'ORDER_USAGE')`
- **Description:** Whether the organization has ever made a real product usage transaction

### `platform_signed_up_date`
- **Type:** Date (YYYY-MM-DD)
- **Source:** Platform DB → `MIN(date) FROM usage_transactions WHERE type = 'GIFT_TOPUP'`
- **Description:** Date of first GIFT_TOPUP transaction (welcome bonus = signup)

### `platform_last_usage_date`
- **Type:** Date (YYYY-MM-DD)
- **Source:** Platform DB → `MAX(date) FROM usage_transactions`
- **Description:** Most recent transaction date

---

## Usage Properties (Always Synced)

### `platform_usage_last_7_days`
- **Type:** Number (as string)
- **Source:** Platform DB → `SUM(ABS(amount)) FROM usage_transactions WHERE type = 'ORDER_USAGE' AND date >= NOW() - 7 days`
- **Description:** Total credits used in last 7 days

### `platform_usage_last_30_days`
- **Type:** Number (as string)
- **Source:** Platform DB → Same query with 30 days
- **Description:** Total credits used in last 30 days

### `platform_usage_trend`
- **Type:** Enum ("up", "stable", "down")
- **Source:** Computed
- **Description:** Compares last 30 days vs previous 30 days
- **Logic:**
  ```python
  change_percent = ((current - previous) / previous) * 100
  if change_percent > 10:  return "up"
  if change_percent < -10: return "down"
  return "stable"
  ```

### `platform_services_used`
- **Type:** String (comma-separated with counts)
- **Source:** Platform DB → `orders` JOIN `services`
- **Format:** `"Service A (45), Service B (12)"`
- **Description:** Services used in last 30 days, sorted by usage count

---

## Testing Properties (Only When `testing_status="testing"`)

These properties are **only synced for organizations actively testing** (have used the
product but have no subscription). They are NOT synced for `"account_created"` orgs
(fresh signups that haven't uploaded anything yet) or `"not_testing"` orgs.

### When is `testing_status="testing"`?

```python
testing_status = (
    "testing" if (
        # No active Paddle subscription AND no subscription history
        (not has_active_subscription and not has_subscription_history)
        # OR has NO_BILLING scope
        or "NO_BILLING" in organization.scopes
    ) and has_used_product  # AND has actually used the product
    else "account_created" if (same billing condition) and not has_used_product
    else "not_testing"
)
```

### `platform_free_credits_remaining`
- **Type:** Number (as string)
- **Source:** Platform DB → `-1 * organizations.usage`
- **Description:** Remaining free credits (negative = overdrawn)
- **Note:** Database stores usage as positive number, we negate for display

### `platform_testing_services_used`
- **Type:** String (comma-separated with counts)
- **Source:** Platform DB → All time services from `orders`
- **Format:** `"Service A (120), Service B (45)"`
- **Description:** All services ever used during testing period

### `platform_testing_succesful_cases`
- **Type:** Number (as string)
- **Source:** Platform DB → `COUNT(*) FROM jobs WHERE job_status = 'Done'`
- **Description:** Total successful job completions (all time)

### `platform_testing_failed_cases`
- **Type:** Number (as string)
- **Source:** Platform DB → `COUNT(*) FROM jobs WHERE job_status = 'Failed'`
- **Description:** Total failed jobs (all time)

> **Note:** `testing_activity_status` was removed from the sync script.
> Use `platform_last_usage_date` in a HubSpot workflow to determine activity
> (e.g. no usage in 7+ days = stalled). See `ARCHITECTURE.md` for examples.

---

## Issues Properties (Always Synced)

### `platform_number_errors_last_30_days`
- **Type:** Number (as string)
- **Source:** Platform DB → `COUNT(*) FROM jobs WHERE job_status = 'Failed' AND timestamp >= NOW() - 30 days`
- **Description:** Failed jobs in last 30 days

### `platform_refunds_last_30_days`
- **Type:** Number (as string)
- **Source:** Platform DB → `COUNT(*) FROM feedback WHERE created_at >= NOW() - 30 days`
- **Description:** Customer feedback/refund requests in last 30 days

---

## Billing Info Enrichment (From Paddle)

When a company's billing address fields are **empty** on HubSpot, they are
automatically filled from the Paddle customer/address data. Once set on
HubSpot (manually or via Paddle), they are **never overwritten** — HubSpot
remains the single source of truth.

| HubSpot Property | Paddle Source | Description |
|------------------|--------------|-------------|
| `name` | Customer/Business name | Company name |
| `country` | Address → `country_code` | ISO 3166-1 alpha-2 (e.g. "US", "GB") |
| `city` | Address → `city` | City |
| `state` | Address → `region` | State, county, or region |
| `zip` | Address → `postal_code` | ZIP / postal code |
| `vat_number` | Business → `tax_identifier` | VAT / tax ID |

**Paddle API endpoints used:**

| Endpoint | Data |
|----------|------|
| `GET /customers/{id}` | Name, email |
| `GET /businesses?customer_id={id}` | Business name, tax identifier |
| `GET /customers/{id}/addresses` | Country, city, region, postal code |

> **Note:** Timezone is not available from the Paddle API. HubSpot typically
> auto-detects timezone from other signals.

---

## Company Status

> **Note:** `company_status` is no longer set by the sync script. It should be
> managed by a HubSpot workflow based on `platform_billing_active`. See
> `ARCHITECTURE.md` → "Design Principle: Raw Data Only" for examples.

---

## Database Schema Reference

### Key Tables

| Table | Purpose |
|-------|---------|
| `organizations` | Org info, admin_user_id, usage (credits), scopes |
| `users` | User accounts, email, organization_id |
| `orders` | Customer orders, timestamp, service_id |
| `jobs` | Processing jobs, job_status (Done/Failed/Submitted) |
| `services` | Service definitions, name |
| `usage_transactions` | Credit transactions (ORDER_USAGE, GIFT_TOPUP, etc.) |
| `feedback` | Customer feedback, refund requests |
| `order_status` | Manual review status (Approved/Rejected/Unapproved) |

### Key Relationships

```
organizations
    ├── users (organization_id)
    │       └── orders (user_id)
    │               ├── jobs (order_id)
    │               └── feedback (order_id)
    └── usage_transactions (organization_id)
```

---

## Adding New Analytics

To add a new analytics property:

1. **Add to model** (`analytics/models.py`):
   ```python
   @dataclass
   class OrganizationAnalytics:
       # ... existing fields ...
       my_new_metric: int = 0
   ```

2. **Add to appropriate computer** (e.g., `analytics/usage_metrics.py`):
   ```python
   # In compute_for_organization():
   metrics.my_new_metric = computed_value
   ```

3. **Add to HubSpot properties** (`analytics/models.py`):
   ```python
   def to_hubspot_properties(self):
       props = {
           # ... existing ...
           "platform_my_new_metric": str(self.my_new_metric),
       }
   ```

4. **Document here** - Add entry in appropriate section above

5. **Create HubSpot property** - In HubSpot Settings → Properties → Companies
