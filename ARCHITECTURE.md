# HubSpot-Platform Sync Architecture

## Overview

This system synchronizes data between our platform and HubSpot CRM. It consists of two distinct workflows:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SYNC WORKFLOWS                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. ORGANIZATION SYNC (sync_organizations.py)                               │
│     Direction: Platform → HubSpot                                           │
│     Purpose: Link platform orgs to HubSpot companies                        │
│                                                                              │
│     ┌──────────┐     ┌─────────────┐     ┌──────────┐                      │
│     │ Platform │ ──► │   Matcher   │ ──► │ HubSpot  │                      │
│     │   DB     │     │   & Linker  │     │Companies │                      │
│     └──────────┘     └─────────────┘     └──────────┘                      │
│                                                                              │
│  2. ANALYTICS SYNC (sync_analytics.py)                                      │
│     Direction: HubSpot → Platform → HubSpot                                 │
│     Purpose: Compute and update analytics for linked companies              │
│                                                                              │
│     ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐       │
│     │ HubSpot  │ ──► │ Platform │ ──► │  Paddle  │ ──► │ HubSpot  │       │
│     │(org IDs) │     │   DB     │     │   API    │     │(update)  │       │
│     └──────────┘     └──────────┘     └──────────┘     └──────────┘       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Entry Points

| Script | Purpose | Starting Point | Frequency |
|--------|---------|----------------|-----------|
| `sync_organizations.py` | Link orgs to companies, create contacts, tasks | Platform DB | Daily/Weekly |
| `sync_analytics.py` | Update analytics properties | HubSpot (companies with org_id) | Hourly/Daily |
| `sync.py` | Combined (legacy) | Platform DB | - |

## Directory Structure

```
hubspot_sync/
├── sync_organizations.py    # Entry point: Platform → HubSpot linking
├── sync_analytics.py        # Entry point: Analytics updates
├── sync.py                  # Combined entry point (legacy)
│
├── config.py                # Configuration & environment
├── filter_config.py         # Blacklists, spam detection rules
│
├── clients/                 # External API clients
│   ├── hubspot.py          # HubSpot CRM API
│   └── platform.py         # Platform database access
│
├── matching/               # Organization ↔ Company matching
│   ├── matcher.py         # Main matching orchestrator
│   ├── signals.py         # Match signal collection
│   └── scorer.py          # Confidence scoring
│
├── actions/                # Business actions
│   ├── linker.py          # Link org to company
│   ├── contact_sync.py    # Create/associate contacts
│   ├── task_creator.py    # Create review tasks
│   ├── company_creator.py # Create placeholder companies
│   └── analytics_sync.py  # Sync analytics to HubSpot
│
├── analytics/              # Analytics computation
│   ├── models.py          # Data models & property definitions
│   ├── platform_analytics.py  # Main orchestrator
│   ├── usage_metrics.py   # Usage from usage_transactions
│   ├── order_metrics.py   # Orders & jobs metrics
│   ├── account_metrics.py # Account info from organizations
│   └── billing_status.py  # Paddle subscription status
│
└── utils/                  # Utilities
    ├── database.py        # DB connection with SSH tunnel
    ├── domains.py         # Domain extraction & validation
    └── audit.py           # Audit logging
```

## Data Flow

### 1. Organization Sync Flow

```
Platform DB (organizations)
    │
    ▼
Filter (blacklist, internal orgs, spam detection)
    │
    ▼
Match to HubSpot company (domain, contacts, Paddle)
    │
    ├─► ALREADY_LINKED: Update analytics, sync contacts
    ├─► AUTO_LINK: Link company, set properties, sync contacts
    ├─► CONFLICT: Create placeholder company + task
    ├─► NO_MATCH: Create placeholder company
    └─► NEEDS_REVIEW: Create task for manual review
```

### 2. Analytics Sync Flow

```
HubSpot API: Get all companies with platform_organization_id
    │
    ▼
For each company, fetch from Platform DB:
    ├── Account metrics (users, admin, credits)
    ├── Usage metrics (transactions, dates)
    └── Order metrics (jobs, services, failures)
    │
    ▼
Fetch from Paddle API:
    └── Billing status (active subscription?)
    │
    ▼
Compute derived metrics:
    ├── is_testing (no subscription + fresh signup OR NO_BILLING scope)
    └── usage_trend (comparing periods)
    │
    ▼
Update HubSpot company properties
```

## Design Principle: Raw Data Only

This sync script acts purely as a **data pump** — it pushes raw, factual data
to HubSpot and does **not** interpret it into business or funnel logic.

All lifecycle management, funnel stages, and automated actions should be
implemented as **HubSpot workflows** that react to the raw properties below.
This keeps business logic in one place (HubSpot), where it can be changed
without code deploys and is visible to the whole team.

### Properties to build HubSpot workflows on

#### Billing & Lifecycle

| Property | Values | Use for |
|----------|--------|---------|
| `platform_billing_active` | `not started`, `active`, `cancelled` | Setting `company_status` (Testing/Customer/Churned), triggering onboarding vs upsell flows |
| `platform_is_testing` | `true`/`false` | Segmenting trial orgs from paying/churned, gating testing-specific workflows |
| `platform_has_account` | `true`/`false` | Distinguishing platform users from other companies |

#### Usage & Engagement

| Property | Type | Use for |
|----------|------|---------|
| `platform_has_used_prodcut` | Boolean | Distinguishing "signed up but never used" from "has tried the product" |
| `platform_last_usage_date` | Date | Detecting stalled/inactive accounts (e.g. no usage in 7+ days) |
| `platform_usage_last_7_days` | Number | Engagement level, activity alerts |
| `platform_usage_last_30_days` | Number | Engagement trends, health scoring |
| `platform_usage_trend` | `up`/`stable`/`down` | Growth signals, churn risk detection |
| `platform_signed_up_date` | Date | Time-based onboarding sequences |

#### Testing / Trial

| Property | Type | Use for |
|----------|------|---------|
| `platform_free_credits_remaining` | Number | Triggering "credits running low" outreach |
| `platform_testing_services_used` | String | Understanding trial engagement depth |
| `platform_testing_succesful_cases` | Number | Qualifying trial success |
| `platform_testing_failed_cases` | Number | Detecting trial friction |

#### Issues

| Property | Type | Use for |
|----------|------|---------|
| `platform_number_errors_last_30_days` | Number | Proactive support outreach |
| `platform_refunds_last_30_days` | Number | Satisfaction/churn risk alerts |

### Example HubSpot workflows

1. **Set company_status automatically:**
   - When `platform_billing_active` = `active` → set `company_status` = "Customer"
   - When `platform_billing_active` = `not started` → set `company_status` = "Testing"
   - When `platform_billing_active` = `cancelled` → set `company_status` = "Churned"

2. **Testing activity detection:**
   - When `platform_last_usage_date` is more than 7 days ago AND `platform_billing_active` = `not started` → mark as stalled, trigger re-engagement
   - When `platform_last_usage_date` is unknown AND `platform_billing_active` = `not started` → mark as not started, trigger onboarding

3. **Conversion signals:**
   - When `platform_free_credits_remaining` < 100 AND `platform_billing_active` = `not started` → trigger sales outreach
   - When `platform_usage_trend` = `up` AND `platform_billing_active` = `not started` → flag as hot lead

---

## See Also

- `ANALYTICS.md` - Detailed analytics property definitions
- `filter_config.py` - Spam detection & blacklist rules
