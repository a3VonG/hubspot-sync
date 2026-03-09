"""
Company creation and enrichment actions.

Creates placeholder companies for unmatched organizations and
enriches them when Paddle data becomes available.
"""

from dataclasses import dataclass
from typing import Optional

from ..clients.hubspot import HubSpotClient, Company
from ..clients.platform import Organization
from ..analytics.billing_status import BillingStatusComputer
from ..config import Config
from ..filter_config import is_likely_spam, get_spam_reason
from ..utils.audit import AuditLog, SyncEventType
from ..utils.domains import extract_domain, is_generic_domain
from .qualify import qualify_account, PROP_QUALIFICATION_STATUS


# Company source values
SOURCE_AUTO_CREATED = "auto_created"
SOURCE_ENRICHED = "enriched_from_paddle"
SOURCE_MANUAL = "manual"


# Property names (adjust if your HubSpot uses different internal names)
PROP_STANDARD_LAB = "standard_lab"
PROP_LIKELY_SPAM = "likely_spam"  # Boolean property to flag potential spam accounts


# =============================================================================
# DEFAULT PROPERTIES FOR NEW COMPANIES
# =============================================================================
# These are set when creating new placeholder companies.
# standard_lab=true marks it as a platform-originated company.
#
DEFAULT_COMPANY_PROPERTIES = {
    PROP_STANDARD_LAB: "true",
}


# Properties to set when enriching a placeholder with Paddle data
ENRICHMENT_PROPERTIES = {
    # Add properties to set after Paddle enrichment if needed
}


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
        billing_computer: Optional[BillingStatusComputer] = None,
    ):
        """
        Initialize the company creator.
        
        Args:
            hubspot: HubSpot API client
            config: Configuration
            audit_log: Audit logger
            billing_computer: Optional Paddle Billing API client for enrichment
        """
        self.hubspot = hubspot
        self.config = config
        self.audit_log = audit_log
        self.billing_computer = billing_computer
    
    def create_or_enrich_company(
        self,
        org: Organization,
        has_active_subscription: bool = False,
        has_real_usage: bool = False,
    ) -> CompanyCreateResult:
        """
        Create a placeholder company or enrich an existing one.
        
        Args:
            org: Platform organization
            has_active_subscription: Whether org has active Paddle subscription
            has_real_usage: Whether org has real platform usage (orders beyond GIFT_TOPUP)
            
        Returns:
            CompanyCreateResult with outcome
        """
        # Check if company already exists for this org
        existing = self.hubspot.get_company_by_platform_org_id(org.id)
        
        if existing:
            # Company exists - check if we should enrich it or update spam status
            return self._maybe_enrich_company(org, existing, has_active_subscription, has_real_usage)
        else:
            # No company exists - create a placeholder
            return self._create_placeholder_company(org, has_active_subscription, has_real_usage)
    
    def _create_placeholder_company(
        self,
        org: Organization,
        has_active_subscription: bool = False,
        has_real_usage: bool = False,
    ) -> CompanyCreateResult:
        """Create a placeholder company for an organization."""
        admin_email = org.admin_email or (org.user_emails[0] if org.user_emails else None)
        
        if not admin_email:
            return CompanyCreateResult(
                success=True,
                message=f"Skipped company for {org.name}: no email available",
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
        
        # Add default properties (configured at top of file)
        properties.update(DEFAULT_COMPANY_PROPERTIES)
        
        # Check for spam indicators
        spam_flag = is_likely_spam(admin_email, has_real_usage, has_active_subscription)
        properties[PROP_LIKELY_SPAM] = "true" if spam_flag else "false"
        
        spam_note = ""
        if spam_flag:
            reason = get_spam_reason(admin_email)
            spam_note = f" [LIKELY SPAM: {reason}]"
        
        # Determine account qualification status
        qualification = qualify_account(
            admin_email,
            has_active_subscription=has_active_subscription,
            config=self.config,
        )
        properties[PROP_QUALIFICATION_STATUS] = qualification
        print(f"  Qualification: {qualification}")
        
        if self.config.dry_run:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,  # Reusing event type
                message=f"[DRY RUN] Would create placeholder company: {company_name}{spam_note}",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return CompanyCreateResult(
                success=True,
                message=f"[DRY RUN] Would create: {company_name}{spam_note}",
                was_created=True,
            )
        
        company, error_detail = self.hubspot.create_company(properties)
        
        if company:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"Created placeholder company: {company_name}{spam_note}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
            )
            return CompanyCreateResult(
                success=True,
                company=company,
                message=f"Created placeholder: {company_name}{spam_note}",
                was_created=True,
            )
        else:
            error_msg = f"Failed to create company for {org.name}: {error_detail}"
            self.audit_log.log(
                SyncEventType.ERROR,
                message=error_msg,
                platform_org_id=org.id,
                platform_org_name=org.name,
                details={"properties": properties, "error": error_detail},
            )
            return CompanyCreateResult(
                success=False,
                message=error_msg,
            )
    
    def _maybe_enrich_company(
        self,
        org: Organization,
        company: Company,
        has_active_subscription: bool = False,
        has_real_usage: bool = False,
    ) -> CompanyCreateResult:
        """
        Enrich a company with Paddle data if appropriate.
        Also clears likely_spam flag if there's real activity.
        
        Only enriches if:
        - Company source is 'auto_created' (placeholder)
        - Paddle data is available
        """
        # Get company with source property and spam flag
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
        current_spam_flag = company_with_source.properties.get(PROP_LIKELY_SPAM, "")
        
        # Check if we should clear spam flag (real activity now exists)
        should_clear_spam = (
            current_spam_flag == "true" and 
            (has_real_usage or has_active_subscription)
        )
        
        if should_clear_spam and not self.config.dry_run:
            self.hubspot.update_company(company.id, {PROP_LIKELY_SPAM: "false"})  # ignore error tuple
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"Cleared likely_spam flag for {company.name or company.id} (now has real activity)",
                platform_org_id=org.id,
                hubspot_company_id=company.id,
            )
        
        # Only enrich auto-created placeholders
        if source != SOURCE_AUTO_CREATED:
            return CompanyCreateResult(
                success=True,
                company=company,
                message=f"Company exists (source: {source or 'manual'}), not enriching",
            )
        
        # Check if we have Paddle data to enrich with
        if not self.billing_computer or not org.paddle_id:
            return CompanyCreateResult(
                success=True,
                company=company,
                message="Placeholder exists, no Paddle data to enrich with",
            )
        
        # Fetch Paddle customer info
        try:
            paddle_info = self.billing_computer.get_customer_info(org.paddle_id)
        except Exception as e:
            print(f"  Warning: Could not fetch Paddle info for {org.paddle_id}: {e}")
            paddle_info = None
        
        if not paddle_info or not paddle_info.get("name"):
            return CompanyCreateResult(
                success=True,
                company=company,
                message="Placeholder exists, Paddle data incomplete",
            )
        
        # Enrich the company
        return self._enrich_company(org, company, paddle_info)
    
    def _enrich_company(
        self, 
        org: Organization, 
        company: Company, 
        paddle_info: dict,
    ) -> CompanyCreateResult:
        """Enrich a placeholder company with Paddle data."""
        paddle_name = paddle_info.get("name")
        
        properties = {
            self.config.company_source_property: SOURCE_ENRICHED,
        }
        
        # Update name if Paddle has company name
        if paddle_name:
            properties["name"] = paddle_name
        
        # Add billing address fields if available
        if paddle_info.get("country_code"):
            properties["country"] = paddle_info["country_code"]
        if paddle_info.get("city"):
            properties["city"] = paddle_info["city"]
        if paddle_info.get("region"):
            properties["state"] = paddle_info["region"]
        if paddle_info.get("postal_code"):
            properties["zip"] = paddle_info["postal_code"]
        if paddle_info.get("tax_identifier"):
            properties["vat_number"] = paddle_info["tax_identifier"]
        
        # Add enrichment properties (configured at top of file)
        properties.update(ENRICHMENT_PROPERTIES)
        
        if self.config.dry_run:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"[DRY RUN] Would enrich company with Paddle data: {paddle_name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
            )
            return CompanyCreateResult(
                success=True,
                company=company,
                message=f"[DRY RUN] Would enrich: {paddle_name}",
                was_enriched=True,
            )
        
        success, error_detail = self.hubspot.update_company(company.id, properties)
        
        if success:
            self.audit_log.log(
                SyncEventType.COMPANY_FOUND,
                message=f"Enriched company with Paddle data: {paddle_name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
                hubspot_company_name=paddle_name,
            )
            # Update company object with new name
            company.name = paddle_name
            return CompanyCreateResult(
                success=True,
                company=company,
                message=f"Enriched with Paddle: {paddle_name}",
                was_enriched=True,
            )
        else:
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to enrich company {company.id}: {error_detail}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company.id,
            )
            return CompanyCreateResult(
                success=False,
                company=company,
                message=f"Failed to enrich company: {error_detail}",
            )
