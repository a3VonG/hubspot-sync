"""
Company linking actions.

Sets the platform_org_id on HubSpot companies.
"""

from dataclasses import dataclass
from typing import Optional

from clients.hubspot import HubSpotClient, Company
from clients.platform import Organization
from config import Config
from analytics.billing_status import BillingStatusComputer
from utils.audit import AuditLog, SyncEventType


@dataclass
class LinkResult:
    """Result of a linking operation."""
    success: bool
    organization: Organization
    company: Company
    message: str
    was_already_linked: bool = False
    status_updated: bool = False


class Linker:
    """
    Links platform organizations to HubSpot companies.
    
    Sets the platform_org_id custom property on the HubSpot company.
    """
    
    def __init__(
        self,
        hubspot: HubSpotClient,
        config: Config,
        audit_log: AuditLog,
        billing_computer: Optional[BillingStatusComputer] = None,
    ):
        """
        Initialize the linker.
        
        Args:
            hubspot: HubSpot API client
            config: Configuration
            audit_log: Audit logger
            billing_computer: Optional billing computer (used to get company name from Paddle when empty)
        """
        self.hubspot = hubspot
        self.config = config
        self.audit_log = audit_log
        self.billing_computer = billing_computer
    
    def link_organization_to_company(
        self,
        org: Organization,
        company: Company,
    ) -> LinkResult:
        """
        Link a platform organization to a HubSpot company.
        
        Sets the platform_org_id on the company. Does NOT set company_status
        or any funnel properties — those are managed by HubSpot workflows
        based on the raw data properties synced by the analytics sync.
        
        Args:
            org: Platform organization
            company: HubSpot company to link to
            
        Returns:
            LinkResult with outcome
        """
        # Check if already linked
        if company.platform_org_id == org.id:
            self.audit_log.log(
                SyncEventType.SKIPPED,
                message=f"Company {company.name} already linked to org {org.id}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
            )
            return LinkResult(
                success=True,
                organization=org,
                company=company,
                message=f"Company {company.name} already linked",
                was_already_linked=True,
            )
        
        # Check for conflict
        if company.platform_org_id and company.platform_org_id != org.id:
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Cannot link: company {company.name} already has platform_org_id={company.platform_org_id}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
                details={"existing_platform_id": company.platform_org_id},
            )
            return LinkResult(
                success=False,
                organization=org,
                company=company,
                message=f"Conflict: company already linked to {company.platform_org_id}",
            )
        
        # If the HubSpot company has no name, try to set it from Paddle
        paddle_name = None
        if not company.name and self.billing_computer and org.paddle_id:
            try:
                paddle_info = self.billing_computer.get_customer_info(
                    org.paddle_id, need_name=True, need_business=True, need_address=False,
                )
                if paddle_info:
                    paddle_name = paddle_info.get("name")
            except Exception as e:
                print(f"  Warning: Could not fetch Paddle customer name for {org.paddle_id}: {e}")
        
        company_display = paddle_name or company.name or f"Company #{company.id}"
        
        # Perform the link (unless dry run)
        if self.config.dry_run:
            name_note = f" (setting name from Paddle: {paddle_name})" if paddle_name else ""
            self.audit_log.log(
                SyncEventType.AUTO_LINKED,
                message=f"[DRY RUN] Would link {org.name} to {company_display}{name_note}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
            )
            return LinkResult(
                success=True,
                organization=org,
                company=company,
                message=f"[DRY RUN] Would link to {company_display}{name_note}",
            )
        
        # Build properties to update (only the link + optional name)
        properties = {
            self.hubspot.platform_org_id_property: org.id,
        }
        if paddle_name:
            properties["name"] = paddle_name
        
        success, error_detail = self.hubspot.update_company(company.id, properties)
        
        if success:
            self.audit_log.log(
                SyncEventType.AUTO_LINKED,
                message=f"Linked {org.name} to {company_display}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
            )
            return LinkResult(
                success=True,
                organization=org,
                company=company,
                message=f"Successfully linked to {company_display}",
            )
        else:
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to link {org.name} to {company_display}: {error_detail}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
                details={"properties": properties, "error": error_detail},
            )
            return LinkResult(
                success=False,
                organization=org,
                company=company,
                message=f"API error linking to {company_display}: {error_detail}",
            )
