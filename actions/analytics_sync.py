"""
Analytics sync action.

Computes platform analytics and syncs them to HubSpot company properties.
"""

from dataclasses import dataclass, field
from typing import Optional

from clients.hubspot import HubSpotClient, Company
from analytics.models import OrganizationAnalytics
from analytics.platform_analytics import PlatformAnalyticsComputer
from config import Config
from filter_config import is_org_blacklisted
from utils.audit import AuditLog, SyncEventType


@dataclass
class AnalyticsSyncResult:
    """Result of analytics sync for an organization."""
    success: bool
    organization_id: str
    company_id: Optional[str] = None
    message: str = ""
    properties_updated: list[str] = field(default_factory=list)


class AnalyticsSyncer:
    """
    Syncs platform analytics to HubSpot companies.
    
    Only syncs analytics for companies that have a platform_org_id set.
    """
    
    def __init__(
        self,
        hubspot: HubSpotClient,
        analytics_computer: PlatformAnalyticsComputer,
        config: Config,
        audit_log: AuditLog,
    ):
        """
        Initialize the analytics syncer.
        
        Args:
            hubspot: HubSpot API client
            analytics_computer: Analytics computer instance
            config: Configuration
            audit_log: Audit logger
        """
        self.hubspot = hubspot
        self.analytics = analytics_computer
        self.config = config
        self.audit_log = audit_log
    
    def sync_organization_analytics(
        self,
        org_id: str,
        paddle_id: Optional[str],
        company: Company,
    ) -> AnalyticsSyncResult:
        """
        Compute and sync analytics for a single organization.
        
        Args:
            org_id: Platform organization ID
            paddle_id: Paddle customer ID (if available)
            company: HubSpot company to update
            
        Returns:
            AnalyticsSyncResult with outcome
        """
        # Check blacklist
        if is_org_blacklisted(org_id):
            return AnalyticsSyncResult(
                success=True,
                organization_id=org_id,
                company_id=company.id,
                message="Skipped: organization is blacklisted",
            )
        
        # Compute analytics
        try:
            analytics = self.analytics.compute_for_organization(org_id, paddle_id)
        except Exception as e:
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to compute analytics for {org_id}: {str(e)}",
                platform_org_id=org_id,
                hubspot_company_id=company.id,
            )
            return AnalyticsSyncResult(
                success=False,
                organization_id=org_id,
                company_id=company.id,
                message=f"Analytics computation failed: {str(e)}",
            )
        
        # Convert to HubSpot properties
        properties = analytics.to_hubspot_properties()
        
        # Print computed properties for visibility
        print(f"  [Analytics] Computed {len(properties)} properties for {org_id}")
        for prop_name, prop_value in sorted(properties.items()):
            # Format the value for display (truncate long values)
            display_value = str(prop_value)
            if len(display_value) > 50:
                display_value = display_value[:47] + "..."
            print(f"    - {prop_name}: {display_value}")
        
        if self.config.dry_run:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"[DRY RUN] Would update analytics for company {company.name}",
                platform_org_id=org_id,
                hubspot_company_id=company.id,
            )
            return AnalyticsSyncResult(
                success=True,
                organization_id=org_id,
                company_id=company.id,
                message=f"[DRY RUN] Would update {len(properties)} properties",
                properties_updated=list(properties.keys()),
            )
        
        # Update HubSpot company
        success, _error = self.hubspot.update_company(company.id, properties)
        
        if success:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"Updated analytics for company {company.name}",
                platform_org_id=org_id,
                hubspot_company_id=company.id,
                details={"properties_count": len(properties)},
            )
            return AnalyticsSyncResult(
                success=True,
                organization_id=org_id,
                company_id=company.id,
                message=f"Updated {len(properties)} properties",
                properties_updated=list(properties.keys()),
            )
        else:
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to update HubSpot company {company.id}",
                platform_org_id=org_id,
                hubspot_company_id=company.id,
            )
            return AnalyticsSyncResult(
                success=False,
                organization_id=org_id,
                company_id=company.id,
                message="HubSpot API update failed",
            )
    
    def sync_organizations_batch(
        self,
        organizations: list[dict],
        companies: dict[str, Company],
    ) -> list[AnalyticsSyncResult]:
        """
        Sync analytics for multiple organizations efficiently.
        
        Args:
            organizations: List of dicts with 'id' and optional 'paddle_id'
            companies: Dictionary mapping org_id to HubSpot Company
            
        Returns:
            List of AnalyticsSyncResult for each org
        """
        results = []
        
        # Filter out blacklisted orgs
        filtered_orgs = [
            org for org in organizations 
            if not is_org_blacklisted(org["id"])
        ]
        
        skipped_count = len(organizations) - len(filtered_orgs)
        if skipped_count > 0:
            print(f"  Skipped {skipped_count} blacklisted organizations")
        
        if not filtered_orgs:
            return results
        
        # Batch compute analytics
        print(f"  Computing analytics for {len(filtered_orgs)} organizations...")
        try:
            analytics_map = self.analytics.compute_for_organizations_batch(filtered_orgs)
        except Exception as e:
            print(f"  ERROR: Batch analytics computation failed: {e}")
            # Fall back to individual computation
            for org in filtered_orgs:
                org_id = org["id"]
                company = companies.get(org_id)
                if company:
                    result = self.sync_organization_analytics(
                        org_id, org.get("paddle_id"), company
                    )
                    results.append(result)
            return results
        
        # Sync to HubSpot
        print(f"  Syncing analytics to HubSpot...")
        for org in filtered_orgs:
            org_id = org["id"]
            company = companies.get(org_id)
            
            if not company:
                results.append(AnalyticsSyncResult(
                    success=False,
                    organization_id=org_id,
                    message="No HubSpot company found",
                ))
                continue
            
            analytics = analytics_map.get(org_id)
            if not analytics:
                results.append(AnalyticsSyncResult(
                    success=False,
                    organization_id=org_id,
                    company_id=company.id,
                    message="Analytics computation returned no data",
                ))
                continue
            
            # Convert and update
            properties = analytics.to_hubspot_properties()
            
            if self.config.dry_run:
                results.append(AnalyticsSyncResult(
                    success=True,
                    organization_id=org_id,
                    company_id=company.id,
                    message=f"[DRY RUN] Would update {len(properties)} properties",
                    properties_updated=list(properties.keys()),
                ))
                continue
            
            success, error_detail = self.hubspot.update_company(company.id, properties)
            
            if success:
                results.append(AnalyticsSyncResult(
                    success=True,
                    organization_id=org_id,
                    company_id=company.id,
                    message=f"Updated {len(properties)} properties",
                    properties_updated=list(properties.keys()),
                ))
            else:
                results.append(AnalyticsSyncResult(
                    success=False,
                    organization_id=org_id,
                    company_id=company.id,
                    message=f"HubSpot API update failed: {error_detail}",
                ))
        
        return results
