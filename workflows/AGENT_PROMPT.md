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
  "count": 12,
  "workflows": [ ...raw HubSpot workflow objects... ]
}
```

## Company lifecycle state machine

These workflows manage `company_status` transitions for platform-connected companies. The statuses and their intended transitions:

```
  ┌────────────┐  sales    ┌─────────┐
  │ Identified │ ────────► │ Engaged │ ─────┐
  └────────────┘           └─────────┘      │
                                            │ billing
    platform_org                            │ active     ┌──────────┐
    + no status                             ├──────────► │ Customer │
         │                                  │            └──────────┘
         ▼                                  │              │    ▲
  ┌─────────────────┐  product   ┌─────────┐│  21d no use  │    │
  │ Account Created │ ─────────► │ Testing │─┘             │    │ usage
  └─────────────────┘  used      └─────────┘               │    │ + billing
         │                          │                      ▼    │
         │  14d stall    14d stall  │                  ┌─────────┐
         └──────────────────────────┼─────────────────►│ Dormant │
                                    └──────────────────┤         │
                                      usage resumed    └─────────┘
                                      (no billing)       │  │
         ┌───────────────────────────────────────────────┘  │
         │  testing_status = account_created                │
         ▼                                                  │
  ┌─────────────────┐                                       │
  │ Account Created │ ◄────────────────────────────────────┘
  └─────────────────┘    usage resumed (with billing) → Customer
```

### Workflow inventory

| Workflow | Trigger | From → To |
|----------|---------|-----------|
| Company Created via Platform → Account Created | `platform_organization_id` IS_KNOWN + `company_status` IS_UNKNOWN | (none) → Account Created |
| Account Created → Testing (Product Used) | `platform_org_id` known + status=Account Created + `platform_testing_status`=testing | Account Created → Testing |
| Identified → Engaged | `company_status` NOT IN [Customer, Disqualified, Dormant, Testing, Account Created, Engaged] + `hs_last_sales_activity_timestamp` in last 30d | Identified → Engaged |
| Testing → Customer (Billing activated) | Branch 1: `platform_org_id` known + status IN [Identified, Engaged, Testing, Account Created] + billing=active; Branch 2: `platform_org_id` known + billing recently changed to active | * → Customer |
| Customer → Dormant (Inactive usage) | `platform_org_id` known + status=Customer + `platform_last_usage_date` > 21d ago | Customer → Dormant |
| Testing → Dormant (Testing Stalled) | `platform_org_id` known + status=Testing + `platform_last_usage_date` > 14d ago | Testing → Dormant |
| Account Created → Dormant (Stalled) | `platform_org_id` known + status=Account Created + `platform_signed_up_date` > 14d ago | Account Created → Dormant |
| Dormant → Testing (Testing Resumed) | `platform_org_id` known + status=Dormant + billing=not started + `platform_usage_last_7_days` > 0 | Dormant → Testing |
| Dormant → Account Created | `platform_org_id` known + status=Dormant + `platform_testing_status`=account_created | Dormant → Account Created |
| Dormant → Customer (usage resumed) | `platform_org_id` known + status=Dormant + billing=active + `platform_usage_last_7_days` > 0 | Dormant → Customer |
| Update Last Company Status Change Date | EVENT_BASED: `company_status` property change | (any) → stamps date |
| Operational: Testing free credits low | `platform_org_id` known + status IN [Identified, Engaged, Testing, Account Created] + `platform_free_credits_remaining` < 20 | Creates TODO task |

### Status values

- **Identified** — company exists in HubSpot, no platform account yet
- **Engaged** — sales touchpoint recorded (meeting booked, call, email)
- **Account Created** — platform account created, hasn't used product yet
- **Testing** — platform account created, has uploaded/used the product (using free credits)
- **Customer** — billing is active
- **Dormant** — was Account Created, Testing, or Customer, but usage/activity stopped
- **Disqualified** — manually excluded (not managed by automation)

## Conventions established

### Naming
All workflows managed by this agent are prefixed with `[agent]`. This allows filtering via `--filter "[agent]"`.

### Re-enrollment
All status-transition workflows MUST have `shouldReEnroll: true` so a company can cycle through states multiple times (e.g. Customer → Dormant → Customer again).

Exception: **Company Created → Account Created** keeps `shouldReEnroll: false` because it triggers on `company_status IS_UNKNOWN`, which is inherently a one-time condition.

### Guards
Every workflow acting on platform-connected companies MUST include `platform_organization_id IS_KNOWN` as an enrollment filter. This prevents the workflow from firing on companies that are not linked to the platform.

Exception: **Identified → Engaged** does not require this guard because it triggers on sales activity, not platform data. Companies in Identified status may not have a platform account.

### Actions pattern
Each status-transition workflow performs these actions in order:
1. **Set `company_status`** to the target status
2. **Set `last_status_change_source`** to `"Automation – <reason>"` for audit trail
3. **Set `last_company_status_change_date`** to execution time
4. **Set `company_status_last_entered_<status>_date`** to execution time (for Testing, Customer, Dormant transitions only — always overwritten)
5. **IF `company_status_entered_<status>_date` IS_UNKNOWN** → set to execution time (first entry only, via LIST_BRANCH)

The **Update Last Company Status Change Date** workflow (event-based) also fires on any `company_status` property change as a safety net.

### Entered-date properties

These track when a company first/last entered each lifecycle stage.

| Property | Set once? | Set by |
|----------|-----------|--------|
| `company_status_entered_identified_date` | Yes (first entry only) | Not automated (Identified set by sync scripts) |
| `company_status_entered_engaged_date` | Yes (first entry only) | Identified→Engaged (IF/THEN branch) |
| `company_status_entered_account_created_date` | Yes (first entry only) | Platform→Account Created, Dormant→Account Created (IF/THEN branch) |
| `company_status_entered_testing_date` | Yes (first entry only) | Account Created→Testing, Dormant→Testing (IF/THEN branch) |
| `company_status_entered_customer_date` | Yes (first entry only) | Testing→Customer, Dormant→Customer (IF/THEN branch) |
| `company_status_entered_dormant_date` | Yes (first entry only) | Customer→Dormant, Testing→Dormant, Account Created→Dormant (IF/THEN branch) |
| `company_status_disqualified_date` | Yes (first entry only) | Manual (not automated) |
| `company_status_last_entered_testing_date` | No (always set) | Dormant→Testing, Account Created→Testing |
| `company_status_last_entered_customer_date` | No (always set) | Dormant→Customer, Testing→Customer |
| `company_status_last_entered_dormant_date` | No (always set) | Customer→Dormant, Testing→Dormant, Account Created→Dormant |

## Key HubSpot properties

### Set by sync scripts (raw data — see ARCHITECTURE.md)
| Property | Type | Source |
|----------|------|--------|
| `platform_organization_id` | string | sync_organizations.py |
| `platform_billing_active` | enum: not started / active / cancelled | sync_analytics.py (Paddle) |
| `platform_testing_status` | enum: account_created / testing / not_testing | sync_analytics.py |
| `platform_last_usage_date` | date | sync_analytics.py |
| `platform_usage_last_7_days` | number | sync_analytics.py |
| `platform_free_credits_remaining` | number | sync_analytics.py |
| `platform_testing_services_used` | string | sync_analytics.py |

### Set by workflows (business logic)
| Property | Type | Set by |
|----------|------|--------|
| `company_status` | enum | Status transition workflows |
| `last_status_change_source` | string | Status transition workflows |
| `last_company_status_change_date` | date | Each transition workflow (inline) + event-based workflow (safety net) |
| `company_status_entered_engaged_date` | date | Identified→Engaged (IF/THEN, once) |
| `company_status_entered_account_created_date` | date | →Account Created workflows (IF/THEN, once) |
| `company_status_entered_testing_date` | date | →Testing workflows (IF/THEN, once) |
| `company_status_entered_customer_date` | date | →Customer workflows (IF/THEN, once) |
| `company_status_entered_dormant_date` | date | →Dormant workflows (IF/THEN, once) |
| `company_status_last_entered_testing_date` | date | →Testing workflows (always) |
| `company_status_last_entered_customer_date` | date | →Customer workflows (always) |
| `company_status_last_entered_dormant_date` | date | →Dormant workflows (always) |
| `company_status_disqualified_date` | date | Manual |

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

### IF/THEN branches (LIST_BRANCH)
To conditionally set a property (e.g. only if empty), use a `LIST_BRANCH` action:
```json
{
  "actionId": "5",
  "listBranches": [{
    "filterBranch": {
      "filterBranches": [{
        "filterBranches": [],
        "filters": [{
          "property": "company_status_entered_testing_date",
          "operation": {
            "operator": "IS_UNKNOWN",
            "includeObjectsWithNoValueSet": false,
            "operationType": "ALL_PROPERTY"
          },
          "filterType": "PROPERTY"
        }],
        "filterBranchType": "AND",
        "filterBranchOperator": "AND"
      }],
      "filters": [],
      "filterBranchType": "OR",
      "filterBranchOperator": "OR"
    },
    "branchName": "If first time entering Testing",
    "connection": { "edgeType": "STANDARD", "nextActionId": "6" }
  }],
  "type": "LIST_BRANCH"
}
```
The branch condition uses the same filter structure as enrollment criteria. If the condition is met, execution follows the branch `connection`. If no branch matches, the workflow ends.

### Setting a date to current time
```json
"fields": {
  "property_name": "last_company_status_change_date",
  "value": { "timestampType": "EXECUTION_TIME", "type": "TIMESTAMP" }
}
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
2. Verify re-enrollment is `true` on all status transitions (except Company Created → Account Created)
3. Verify `platform_organization_id IS_KNOWN` guard on all platform-data workflows
4. Verify each status transition sets: `company_status`, `last_status_change_source`, `last_company_status_change_date`, the appropriate entered/last-entered dates
5. Verify IF/THEN branches check `IS_UNKNOWN` before setting entered dates
6. Run `python -m workflows update --dry-run` before the real push
7. After pushing, regenerate docs: `python -m workflows markdown`
