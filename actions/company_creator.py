"""
Company creation and enrichment actions.

Creates placeholder companies for unmatched organizations and
enriches them when Paddle data becomes available.
"""

from dataclasses import dataclass
from typing import Optional

from clients.hubspot import HubSpotClient, Company
from clients.platform import Organization
from clients.paddle import PaddleClient, PaddleSubscription
from config import Config
from utils.audit import AuditLog, SyncEventType
from utils.domains import extract_domain, is_generic_domain


# Company source values
SOURCE_AUTO_CREATED = "auto_created"
SOURCE_ENRICHED = "enriched_from_paddle"
SOURCE_MANUAL = "manual"


@dataclass
class CompanyCreateResult:
    """Result of company creation."""
    success: bool
    company: Optional[Company] = None
    message: str = ""
    was_created: bool = False
    was_enriched: bool = False


class CompanyCreator:
    """
    Creates and enriches HubSpot companies for platform organizations.
    
    Handles:
    - Creating placeholder companies for orgs without matches
    - Enriching placeholder companies when Paddle data becomes available
    - Respecting manual edits (not overwriting non-placeholder companies)
    """
    
    def __init__(
        self,
        hubspot: HubSpotClient,
        config: Config,
        audit_log: AuditLog,
        paddle: Optional[PaddleClient] = None,
    ):
        """
        Initialize the company creator.
        
        Args:
            hubspot: HubSpot API client
            config: Configuration
            audit_log: Audit logger
            paddle: Optional Paddle API client for enrichment
        """
        self.hubspot = hubspot
        self.config = config
        self.audit_log = audit_log
        self.paddle = paddle
    
    def create_or_enrich_company(self, org: Organization) -> CompanyCreateResult:
        """
        Create a placeholder company or enrich an existing one.
        
        Args:
            org: Platform organization
            
        Returns:
            CompanyCreateResult with outcome
        """
        # Check if company already exists for this org
        existing = self.hubspot.get_company_by_platform_org_id(org.id)
        
        if existing:
            # Company exists - check if we should enrich it
            return self._maybe_enrich_company(org, existing)
        else:
            # No company exists - create a placeholder
            return self._create_placeholder_company(org)
    
    def _create_placeholder_company(self, org: Organization) -> CompanyCreateResult:
        """Create a placeholder company for an organization."""
        admin_email = org.admin_email or (org.user_emails[0] if org.user_emails else None)
        
        if not admin_email:
            return CompanyCreateResult(
                success=False,
                message=f"Cannot create company for {org.name}: no email available",
            )
        
        # Build company name
        company_name = f'[placeholder company from "{admin_email}"]'
        
        # Extract domain if not generic
        domain = extract_domain(admin_email)
        if domain and is_generic_domain(domain, self.config):
            domain = None  # Don't use generic domains
        
        # Build properties
        properties = {
            "name": company_name,
            self.hubspot.platform_org_id_property: org.id,
            self.config.company_source_property: SOURCE_AUTO_CREATED,
        }
        
        if domain:
            properties["domain"] = domain
        
        # TODO: Set additional properties like "standard_lab" checkbox
        # Print for now so user knows where to add them
        print(f"  [TODO] Set additional properties for placeholder company: {org.name}")
        print(f"         - standard_lab checkbox")
        print(f"         - Any other default properties")
        
        if self.config.dry_run:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,  # Reusing event type
                message=f"[DRY RUN] Would create placeholder company: {company_name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return CompanyCreateResult(
                success=True,
                message=f"[DRY RUN] Would create: {company_name}",
                was_created=True,
            )
        
        company = self.hubspot.create_company(properties)
        
        if company:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"Created placeholder company: {company_name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
            )
            return CompanyCreateResult(
                success=True,
                company=company,
                message=f"Created placeholder: {company_name}",
                was_created=True,
            )
        else:
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to create placeholder company for {org.name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return CompanyCreateResult(
                success=False,
                message=f"Failed to create company for {org.name}",
            )
    
    def _maybe_enrich_company(self, org: Organization, company: Company) -> CompanyCreateResult:
        """
        Enrich a company with Paddle data if appropriate.
        
        Only enriches if:
        - Company source is 'auto_created' (placeholder)
        - Paddle data is available
        """
        # Get company with source property
        company_with_source = self.hubspot.get_company_with_source(
            company.id, 
            self.config.company_source_property
        )
        
        if not company_with_source:
            return CompanyCreateResult(
                success=True,
                company=company,
                message="Company exists, could not fetch source",
            )
        
        source = company_with_source.properties.get(self.config.company_source_property)
        
        # Only enrich auto-created placeholders
        if source != SOURCE_AUTO_CREATED:
            return CompanyCreateResult(
                success=True,
                company=company,
                message=f"Company exists (source: {source or 'manual'}), not enriching",
            )
        
        # Check if we have Paddle data to enrich with
        if not self.paddle or not org.paddle_id:
            return CompanyCreateResult(
                success=True,
                company=company,
                message="Placeholder exists, no Paddle data to enrich with",
            )
        
        # Fetch Paddle data
        paddle_data = self.paddle.get_subscription_by_id(org.paddle_id)
        
        if not paddle_data or not paddle_data.company_name:
            return CompanyCreateResult(
                success=True,
                company=company,
                message="Placeholder exists, Paddle data incomplete",
            )
        
        # Enrich the company
        return self._enrich_company(org, company, paddle_data)
    
    def _enrich_company(
        self, 
        org: Organization, 
        company: Company, 
        paddle_data: PaddleSubscription,
    ) -> CompanyCreateResult:
        """Enrich a placeholder company with Paddle data."""
        properties = {
            self.config.company_source_property: SOURCE_ENRICHED,
        }
        
        # Update name if Paddle has company name
        if paddle_data.company_name:
            properties["name"] = paddle_data.company_name
        
        # Add country if available
        if paddle_data.country:
            properties["country"] = paddle_data.country
        
        # TODO: Set additional properties after enrichment
        print(f"  [TODO] Set additional properties after enrichment: {org.name}")
        print(f"         - Update standard_lab or other checkboxes if needed")
        
        if self.config.dry_run:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"[DRY RUN] Would enrich company with Paddle data: {paddle_data.company_name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
            )
            return CompanyCreateResult(
                success=True,
                company=company,
                message=f"[DRY RUN] Would enrich: {paddle_data.company_name}",
                was_enriched=True,
            )
        
        success = self.hubspot.update_company(company.id, properties)
        
        if success:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"Enriched company with Paddle data: {paddle_data.company_name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=paddle_data.company_name,
            )
            # Update company object with new name
            company.name = paddle_data.company_name
            return CompanyCreateResult(
                success=True,
                company=company,
                message=f"Enriched with Paddle: {paddle_data.company_name}",
                was_enriched=True,
            )
        else:
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to enrich company {company.id}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
            )
            return CompanyCreateResult(
                success=False,
                company=company,
                message="Failed to enrich company",
            )
