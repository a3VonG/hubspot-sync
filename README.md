# HubSpot-Platform Sync

Synchronizes platform organizations with HubSpot companies, creating associations between contacts and companies, and handling edge cases through HubSpot Tasks.

## Features

- **Automatic Matching**: Links platform organizations to HubSpot companies using multiple signals:
  - Existing `platform_org_id` (ground truth)
  - Email domain matching
  - Contact associations
  - Paddle company name (if available)

- **Conflict Resolution**: Creates HubSpot Tasks for manual review when:
  - Multiple companies match an organization
  - A company is already linked to a different organization
  - Match confidence is below threshold

- **Contact Sync**: Creates HubSpot contacts and associates them with the linked company

- **Slack Reporting**: Sends sync summaries to Slack

- **Audit Logging**: SQLite-based logging for debugging and compliance

## Project Structure

```
hubspot_sync/
├── config.py              # Configuration from environment
├── sync.py                # Main orchestrator
├── clients/
│   ├── platform.py        # Platform database client
│   ├── hubspot.py         # HubSpot API client
│   └── paddle.py          # Paddle API client (optional)
├── matching/
│   ├── matcher.py         # Core matching logic
│   ├── signals.py         # Signal collectors
│   └── scorer.py          # Confidence scoring
├── actions/
│   ├── linker.py          # Link org to company
│   ├── contact_sync.py    # Create/associate contacts
│   └── task_creator.py    # Create HubSpot tasks
├── utils/
│   ├── domains.py         # Domain utilities
│   └── audit.py           # Audit logging
├── tests/                 # Test suite
├── .github/workflows/     # GitHub Actions
└── requirements.txt
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

### Run Sync

```bash
# Full sync
python sync.py

# Dry run (preview changes)
python sync.py --dry-run

# Sync specific organization
python sync.py --org-id "uuid-here"
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
pytest tests/ -v --cov=. --cov-report=term-missing
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