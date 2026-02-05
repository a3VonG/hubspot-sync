"""
Company linking actions.

Sets the platform_org_id on HubSpot companies.
"""

from dataclasses import dataclass

from clients.hubspot import HubSpotClient, Company
from clients.platform import Organization
from config import Config
from utils.audit import AuditLog, SyncEventType


@dataclass
class LinkResult:
    """Result of a linking operation."""
    success: bool
    organization: Organization
    company: Company
    message: str
    was_already_linked: bool = False


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
    ):
        """
        Initialize the linker.
        
        Args:
            hubspot: HubSpot API client
            config: Configuration
            audit_log: Audit logger
        """
        self.hubspot = hubspot
        self.config = config
        self.audit_log = audit_log
    
    def link_organization_to_company(
        self,
        org: Organization,
        company: Company,
    ) -> LinkResult:
        """
        Link a platform organization to a HubSpot company.
        
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
        
        # Perform the link (unless dry run)
        if self.config.dry_run:
            self.audit_log.log(
                SyncEventType.AUTO_LINKED,
                message=f"[DRY RUN] Would link {org.name} to {company.name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
            )
            return LinkResult(
                success=True,
                organization=org,
                company=company,
                message=f"[DRY RUN] Would link to {company.name}",
            )
        
        success = self.hubspot.update_company_platform_org_id(company.id, org.id)
        
        if success:
            self.audit_log.log(
                SyncEventType.AUTO_LINKED,
                message=f"Linked {org.name} to {company.name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
            )
            return LinkResult(
                success=True,
                organization=org,
                company=company,
                message=f"Successfully linked to {company.name}",
            )
        else:
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to update company {company.name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
            )
            return LinkResult(
                success=False,
                organization=org,
                company=company,
                message=f"API error linking to {company.name}",
            )
