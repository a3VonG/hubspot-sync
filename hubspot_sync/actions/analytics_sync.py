"""
Analytics sync action.

Computes platform analytics and syncs them to HubSpot company properties.
Also fills in empty Paddle billing fields (VAT, address) if available.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..clients.hubspot import HubSpotClient, Company
from ..analytics.models import OrganizationAnalytics
from ..analytics.billing_status import BillingStatusComputer
from ..analytics.platform_analytics import PlatformAnalyticsComputer
from ..config import Config
from ..filter_config import is_org_blacklisted
from ..utils.audit import AuditLog, SyncEventType


# HubSpot properties that can be filled from Paddle billing data
PADDLE_BILLING_PROPERTIES = ["country", "city", "state", "zip", "vat_number"]


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
    Also fills in empty billing fields (VAT, address) from Paddle when available.
    """
    
    def __init__(
        self,
        hubspot: HubSpotClient,
        analytics_computer: PlatformAnalyticsComputer,
        config: Config,
        audit_log: AuditLog,
        billing_computer: Optional[BillingStatusComputer] = None,
    ):
        """
        Initialize the analytics syncer.
        
        Args:
            hubspot: HubSpot API client
            analytics_computer: Analytics computer instance
            config: Configuration
            audit_log: Audit logger
            billing_computer: Optional Paddle Billing API client for filling empty fields
        """
        self.hubspot = hubspot
        self.analytics = analytics_computer
        self.config = config
        self.audit_log = audit_log
        self.billing_computer = billing_computer
    
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
        
        # Fill empty Paddle billing fields (VAT, address, name) if available
        if self.billing_computer and paddle_id:
            self._fill_empty_billing_fields(company, paddle_id, properties)
        
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
    
    def _fill_empty_billing_fields(
        self,
        company: Company,
        paddle_id: str,
        properties: dict,
    ):
        """
        Check and fill empty Paddle billing fields on the HubSpot company.
        
        Fetches the company's current billing properties from HubSpot,
        checks which are empty, and fills them from Paddle (only what's needed).
        Only sets a field if it is currently empty, so manual edits are preserved.
        """
        # Fetch current billing properties from HubSpot
        try:
            company_with_billing = self.hubspot.get_company_by_platform_org_id(
                company.platform_org_id or properties.get("platform_organization_id", ""),
                extra_properties=PADDLE_BILLING_PROPERTIES,
            )
        except Exception:
            return  # Can't check, skip silently
        
        if not company_with_billing:
            return
        
        props = company_with_billing.properties or {}
        
        def is_empty(key: str) -> bool:
            val = props.get(key)
            return not val or not str(val).strip()
        
        # Determine which Paddle API calls are needed
        name_empty = is_empty("name") and not company_with_billing.name
        vat_empty = is_empty("vat_number")
        address_empty = (
            is_empty("country") or is_empty("city")
            or is_empty("state") or is_empty("zip")
        )
        
        need_name = name_empty
        need_business = name_empty or vat_empty
        need_address = address_empty
        
        if not (need_name or need_business or need_address):
            return  # All billing fields already populated
        
        # Fetch from Paddle (only the endpoints we need)
        try:
            paddle_info = self.billing_computer.get_customer_info(
                paddle_id,
                need_name=need_name,
                need_business=need_business,
                need_address=need_address,
            )
        except Exception as e:
            print(f"  Warning: Could not fetch Paddle customer info: {e}")
            return
        
        if not paddle_info:
            return
        
        # Fill empty fields
        field_mapping = [
            ("name", "name", "Name"),
            ("country", "country_code", "Country"),
            ("city", "city", "City"),
            ("state", "region", "State/Region"),
            ("zip", "postal_code", "Postal Code"),
            ("vat_number", "tax_identifier", "VAT Number"),
        ]
        
        filled = []
        for hs_prop, paddle_key, label in field_mapping:
            paddle_value = paddle_info.get(paddle_key)
            if not paddle_value:
                continue
            
            current_value = props.get(hs_prop)
            if hs_prop == "name":
                current_value = current_value or company_with_billing.name
            
            if not current_value or not str(current_value).strip():
                properties[hs_prop] = paddle_value
                filled.append(f"{label}={paddle_value}")
        
        if filled:
            print(f"  Setting from Paddle (was empty): {', '.join(filled)}")
    
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
