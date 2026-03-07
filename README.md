# Sales Enablement Tooling

A collection of sales enablement modules for syncing platform data with HubSpot CRM, managing HubSpot workflows, and outbound lead generation.

## Modules

### 1. `hubspot_sync/` - Platform-to-HubSpot Sync

The core sync engine that links platform organizations to HubSpot companies, syncs contacts, computes analytics, and handles conflicts via HubSpot Tasks.

**Features:**
- **Automatic Matching**: Links organizations using domain matching, contact associations, Paddle data
- **Conflict Resolution**: Creates HubSpot Tasks for manual review
- **Contact Sync**: Creates and associates HubSpot contacts
- **Analytics Sync**: Computes and pushes usage/billing metrics to HubSpot
- **Audit Logging**: SQLite-based logging for debugging

### 2. `workflows/` - HubSpot Workflow Management

Collaborative fetch-edit-preview-update cycle for managing HubSpot automation workflows. See [workflows/AGENT_PROMPT.md](workflows/AGENT_PROMPT.md).

### 3. `outbound/` - Outbound Lead Generation (Early Development)

Agent-driven outbound lead enrichment and generation. See [outbound/brainstorm.md](outbound/brainstorm.md).

## Project Structure

```
.
├── hubspot_sync/              # Platform-to-HubSpot sync module
│   ├── __init__.py
│   ├── __main__.py            # Entry point: python -m hubspot_sync
│   ├── sync.py                # Legacy combined entry point
│   ├── sync_organizations.py  # Organization linking workflow
│   ├── sync_analytics.py      # Analytics refresh workflow
│   ├── config.py              # Configuration from environment
│   ├── filter_config.py       # Blacklist/spam filtering
│   ├── clients/               # External API clients
│   │   ├── platform.py        # Platform database client
│   │   └── hubspot.py         # HubSpot API client
│   ├── matching/              # Organization-to-company matching
│   │   ├── matcher.py         # Core matching logic
│   │   ├── signals.py         # Signal collectors
│   │   └── scorer.py          # Confidence scoring
│   ├── actions/               # Post-matching operations
│   │   ├── linker.py          # Link org to company
│   │   ├── company_creator.py # Create/enrich placeholder companies
│   │   ├── contact_sync.py    # Create/associate contacts
│   │   ├── task_creator.py    # Create HubSpot tasks
│   │   └── analytics_sync.py  # Analytics sync action
│   ├── analytics/             # Analytics computation
│   │   ├── models.py          # Analytics data models
│   │   ├── platform_analytics.py
│   │   ├── account_metrics.py
│   │   ├── order_metrics.py
│   │   ├── usage_metrics.py
│   │   └── billing_status.py  # Paddle billing status
│   └── utils/                 # Shared utilities
│       ├── database.py        # DB connection with SSH tunnel
│       ├── domains.py         # Domain utilities
│       └── audit.py           # Audit logging
├── workflows/                 # HubSpot workflow management
│   ├── __init__.py
│   ├── __main__.py            # Entry point: python -m workflows
│   ├── client.py              # HubSpot Automation API v4
│   ├── manager.py             # Workflow manager
│   └── data/                  # Workflow JSON storage
├── outbound/                  # Outbound lead generation (WIP)
│   └── brainstorm.md
├── tests/                     # Test suite
├── .github/workflows/         # GitHub Actions (CI/CD)
├── requirements.txt
├── ARCHITECTURE.md
└── ANALYTICS.md
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required environment variables:
- `HUBSPOT_API_KEY`: HubSpot private app access token
- `PLATFORM_DB_URL`: PostgreSQL connection string

Optional:
- `PADDLE_API_KEY` and `PADDLE_VENDOR_ID`: For Paddle enrichment
- `SLACK_WEBHOOK_URL`: For Slack notifications
- `DRY_RUN`: Set to `true` to preview changes

### 3. HubSpot Setup

Create a custom property on Companies:
- Property name: `platform_org_id` (or configure via `HUBSPOT_PLATFORM_ORG_ID_PROPERTY`)
- Type: Single-line text

Required HubSpot scopes for your private app:
- `crm.objects.contacts.read`
- `crm.objects.contacts.write`
- `crm.objects.companies.read`
- `crm.objects.companies.write`

## Usage

### HubSpot Sync

There are two separate workflows that can be run independently:

#### 1. Organization Sync (matching & linking)

Matches platform organizations to HubSpot companies, creates placeholder companies, links them, and syncs contacts. Run this periodically (e.g. daily/weekly) to onboard new organizations.

```bash
# Full org sync
python -m hubspot_sync.sync_organizations

# Dry run (preview changes)
python -m hubspot_sync.sync_organizations --dry-run

# Sync specific organization
python -m hubspot_sync.sync_organizations --org-id "uuid-here"

# Limit number of orgs processed
python -m hubspot_sync.sync_organizations --limit 50
```

#### 2. Analytics Sync (refresh properties)

Updates analytics properties for companies that are already linked. Queries HubSpot for all companies with a `platform_organization_id`, fetches fresh data from the platform DB and Paddle, then pushes updated properties back. Run this more frequently (e.g. hourly/daily).

```bash
# Full analytics refresh
python -m hubspot_sync.sync_analytics

# Dry run (preview changes)
python -m hubspot_sync.sync_analytics --dry-run

# Update specific organization
python -m hubspot_sync.sync_analytics --org-id "uuid-here"

# Limit companies processed
python -m hubspot_sync.sync_analytics --limit 100

# Minimal output
python -m hubspot_sync.sync_analytics --quiet
```

See [ANALYTICS.md](ANALYTICS.md) for property definitions and logic.

#### Legacy Combined Script

Runs both workflows together. Prefer the separated scripts above for production use.

```bash
python -m hubspot_sync              # Both workflows
python -m hubspot_sync --dry-run    # Preview
```

### Workflows

```bash
python -m workflows                 # Manage HubSpot workflows
```

### GitHub Actions

The sync runs automatically every 6 hours via GitHub Actions. Configure secrets:
- `HUBSPOT_API_KEY`
- `PLATFORM_DB_URL`
- `PADDLE_API_KEY` (optional)
- `PADDLE_VENDOR_ID` (optional)
- `SLACK_WEBHOOK_URL` (optional)

Trigger manually from Actions tab with optional dry-run or org-id parameters.

## Matching Logic

### Signal Hierarchy (by confidence)

| Signal | Confidence | Description |
|--------|------------|-------------|
| Existing platform_org_id | 1.0 | Company already linked (ground truth) |
| Admin email domain match | 0.85 | Admin's email domain matches company domain |
| User email domain match | 0.70 | Any user's email domain matches |
| Contact association | 0.40-0.80 | Proportional to matched users |
| Paddle company name | 0.75 | Paddle data matches HubSpot company |

### Match Outcomes

| Outcome | Condition | Action |
|---------|-----------|--------|
| ALREADY_LINKED | Company has matching platform_org_id | Sync contacts only |
| AUTO_LINK | Single match with confidence ≥ 0.8 | Link and sync contacts |
| NEEDS_REVIEW | Single match with confidence 0.4-0.8 | Create review task |
| MULTIPLE_MATCHES | Multiple strong candidates | Create task to choose |
| CONFLICT | Company has different platform_org_id | Create conflict task |
| NO_MATCH | No candidates found | Log (optionally create task) |

## Testing

```bash
# Install test dependencies
pip install pytest pytest-cov

# Run tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=hubspot_sync --cov-report=term-missing
```

## Slack Report Format

After each sync, a report is sent to Slack (if configured):

```
✅ HubSpot Sync Complete

Organizations: 150
Auto-linked: 12
Already linked: 95
Tasks created: 8
Contacts created: 45
Contacts associated: 120
```

## Troubleshooting

### Check Audit Log

```python
import sqlite3
conn = sqlite3.connect('sync_audit.db')
# Get recent errors
cursor = conn.execute("""
    SELECT timestamp, platform_org_name, message 
    FROM sync_events 
    WHERE event_type = 'error'
    ORDER BY timestamp DESC 
    LIMIT 10
""")
for row in cursor:
    print(row)
```

### Common Issues

1. **No matches found**: Check if contacts exist in HubSpot with matching emails
2. **Conflicts**: Review HubSpot Tasks and manually resolve
3. **API errors**: Verify HubSpot API key and scopes

---

## Architecture Background

This sync system was designed to handle complex matching scenarios:

- Users can move between organizations
- Companies may be created in HubSpot before or after platform signup
- Multiple signals are combined to determine matches
- Conflicts require human review via HubSpot Tasks 