"""
HubSpot Sync - Platform organization sync to HubSpot CRM.

Modules:
    - actions: Post-matching operations (linking, contact sync, company creation)
    - analytics: Platform analytics computation
    - clients: External API clients (HubSpot, Platform DB)
    - matching: Organization-to-company matching logic
    - utils: Shared utilities (database, domains, audit logging)

Entry points:
    python -m hubspot_sync                     # Combined sync (legacy)
    python -m hubspot_sync.sync_organizations  # Organization linking workflow
    python -m hubspot_sync.sync_analytics      # Analytics refresh workflow
"""
