# Workflow Agent Prompt

Read this file at the start of every session involving HubSpot workflows.

## What this module does

The `workflows/` package manages HubSpot automation workflows through a **fetch → edit → preview → update** cycle. We fetch workflow JSON from HubSpot, collaboratively edit it in `working.json`, preview the diff, and push only the changes back.

```
python -m workflows fetch --filter "[agent]"   # pull tagged workflows
python -m workflows preview                     # diff working vs original
python -m workflows update --dry-run            # see what would push
python -m workflows update                      # push to HubSpot
python -m workflows markdown                    # generate docs
```

Always activate the venv first: `source venv/bin/activate`

## Data files

```
workflows/data/
  original.json   ← fetched from HubSpot (do not edit)
  working.json    ← our editable copy (edit this)
  workflows.md    ← generated markdown docs
```

Both JSON files share the same envelope structure:
```json
{
  "fetched_at": "...",
  "source": "[agent]",
  "count": 9,
  "workflows": [ ...raw HubSpot workflow objects... ]
}
```

## Company lifecycle state machine

These workflows manage `company_status` transitions for platform-connected companies. The statuses and their intended transitions:

```
                    ┌──────────────────────────────────────────┐
                    │                                          │
                    ▼                                          │
  ┌────────────┐  touchpoint  ┌─────────┐  billing   ┌──────────┐
  │ Identified │ ──────────► │ Engaged │ ─────────► │ Customer │
  └────────────┘              └─────────┘             └──────────┘
        │                         │                     │    ▲
        │         billing active  │                     │    │
        └─────────────────────────┼─────────────────────┘    │
                                  │                          │
                    platform_org  │    21d no usage          │ usage + billing
                    + no status   │  ┌──────────────┐       │
                         │        │  │              │       │
                         ▼        ▼  ▼              │       │
                    ┌─────────┐     ┌─────────┐     │       │
                    │ Testing │ ──► │ Dormant │ ────┘       │
                    └─────────┘     └─────────┘             │
                      ▲  14d stall    ▲  │                  │
                      │               │  │  usage + no bill │
                      │               │  └──────────────────┘
                      │               │
                      └───────────────┘
                        usage resumed
                        (no billing)
```

### Workflow inventory

| Workflow | Trigger | From → To |
|----------|---------|-----------|
| Company Created via Platform → Testing | `platform_organization_id` IS_KNOWN + `company_status` IS_UNKNOWN | (none) → Testing |
| Identified → Engaged (touchpoint) | `company_status` NOT IN [Customer, Disqualified, Dormant, Testing] + `hs_last_sales_activity_timestamp` in last 30d | Identified → Engaged |
| Testing → Customer (Billing activated) | Branch 1: `platform_org_id` known + status IN [Identified, Engaged, Testing] + billing=active; Branch 2: `platform_org_id` known + billing recently changed to active | * → Customer |
| Customer → Dormant (Inactive usage) | `platform_org_id` known + status=Customer + `platform_last_usage_date` > 21d ago | Customer → Dormant |
| Testing → Dormant (Testing Stalled) | `platform_org_id` known + status=Testing + `platform_is_testing`=true + `platform_last_usage_date` > 14d ago | Testing → Dormant |
| Dormant → Testing (Testing Resumed) | `platform_org_id` known + status=Dormant + billing=not started + `platform_usage_last_7_days` > 0 | Dormant → Testing |
| Dormant → Customer (usage resumed up) | `platform_org_id` known + status=Dormant + billing=active + `platform_usage_last_7_days` > 0 | Dormant → Customer |
| Update Last Company Status Change Date | EVENT_BASED: `company_status` property change | (any) → stamps date |
| Operational: Testing free credits low | `platform_org_id` known + status IN [Identified, Engaged, Testing] + `platform_free_credits_remaining` < 20 | Creates TODO task |

### Status values

- **Identified** — company exists in HubSpot, no platform account yet
- **Engaged** — sales touchpoint recorded (meeting, call, email)
- **Testing** — platform account created, using free credits
- **Customer** — billing is active
- **Dormant** — was Testing or Customer, but usage stopped
- **Disqualified** — manually excluded (not managed by automation)

## Conventions established

### Naming
All workflows managed by this agent are prefixed with `[agent]`. This allows filtering via `--filter "[agent]"`.

### Re-enrollment
All status-transition workflows MUST have `shouldReEnroll: true` so a company can cycle through states multiple times (e.g. Customer → Dormant → Customer again).

Exception: **Company Created → Testing** keeps `shouldReEnroll: false` because it triggers on `company_status IS_UNKNOWN`, which is inherently a one-time condition.

### Guards
Every workflow acting on platform-connected companies MUST include `platform_organization_id IS_KNOWN` as an enrollment filter. This prevents the workflow from firing on companies that are not linked to the platform.

Exception: **Identified → Engaged** does not require this guard because it triggers on sales activity, not platform data. Companies in Identified status may not have a platform account.

### Actions pattern
Each status-transition workflow performs exactly two actions:
1. **Set `company_status`** to the target status
2. **Set `last_status_change_source`** to `"Automation – <reason>"` for audit trail

The **Update Last Company Status Change Date** workflow is event-based and fires on any `company_status` property change to stamp `last_company_status_change_date`.

## Key HubSpot properties

### Set by sync scripts (raw data — see ARCHITECTURE.md)
| Property | Type | Source |
|----------|------|--------|
| `platform_organization_id` | string | sync_organizations.py |
| `platform_billing_active` | enum: not started / active / cancelled | sync_analytics.py (Paddle) |
| `platform_is_testing` | enum: true / false | sync_analytics.py |
| `platform_last_usage_date` | date | sync_analytics.py |
| `platform_usage_last_7_days` | number | sync_analytics.py |
| `platform_free_credits_remaining` | number | sync_analytics.py |
| `platform_testing_services_used` | string | sync_analytics.py |

### Set by workflows (business logic)
| Property | Type | Set by |
|----------|------|--------|
| `company_status` | enum | Status transition workflows |
| `last_status_change_source` | string | Status transition workflows |
| `last_company_status_change_date` | date | Update date workflow |

## HubSpot API notes

- **API version**: Automation v4 (beta) — `POST /automation/v4/flows/{flowId}`
- **Auth**: Bearer token via Private App with `automation` scope (env: `HUBSPOT_API_KEY`)
- **Folder filtering**: The v4 API does NOT expose folder membership. We use name-based filtering (`--filter`) instead.
- **Rate limits**: The client handles 429 responses with automatic retry.
- **Update payload**: When pushing updates, we fetch the latest `revisionId` first to avoid conflicts.

## Editing workflow JSON

When editing `working.json`, the key sections to modify are:

### Changing enrollment criteria
```json
"enrollmentCriteria": {
  "shouldReEnroll": true,
  "listFilterBranch": {
    "filterBranches": [{
      "filters": [
        { "property": "...", "operation": { "operator": "IS_KNOWN", ... }, "filterType": "PROPERTY" }
      ],
      "filterBranchType": "AND"
    }],
    "filterBranchType": "OR"
  }
}
```
- Top-level `filterBranches` are OR'd (enrollment branches)
- Filters within a branch are AND'd
- Multiple branches = OR conditions

### Changing actions
```json
"actions": [{
  "actionId": "1",
  "actionTypeId": "0-5",  // Set Property
  "fields": {
    "property_name": "company_status",
    "value": { "staticValue": "Customer", "type": "STATIC_VALUE" }
  }
}]
```

### Action type IDs
| ID | Action |
|----|--------|
| 0-5 | Set Property |
| 0-3 | Create Task |
| 0-1 | Delay |
| 0-4 | Send Automated Email |

## Checklist before pushing

1. Run `python -m workflows preview` and review every change
2. Verify re-enrollment is `true` on all status transitions (except Company Created → Testing)
3. Verify `platform_organization_id IS_KNOWN` guard on all platform-data workflows
4. Verify each status transition sets both `company_status` AND `last_status_change_source`
5. Run `python -m workflows update --dry-run` before the real push
6. After pushing, regenerate docs: `python -m workflows markdown`
