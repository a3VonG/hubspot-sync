"""
Signal extraction for matching organizations to companies.

Signals are individual pieces of evidence that a platform organization
should be linked to a HubSpot company.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from clients.hubspot import HubSpotClient, Company, Contact
from clients.platform import Organization
from analytics.billing_status import BillingStatusComputer
from config import Config
from utils.domains import extract_domain, is_generic_domain


class SignalType(str, Enum):
    """Types of matching signals."""
    EXISTING_PLATFORM_ID = "existing_platform_id"  # Company already has this org's ID
    DOMAIN_MATCH = "domain_match"  # Admin email domain matches company domain
    CONTACT_ASSOCIATION = "contact_association"  # User contact is associated with company
    PADDLE_NAME_MATCH = "paddle_name_match"  # Paddle company name matches HubSpot
    PADDLE_VAT_MATCH = "paddle_vat_match"  # Paddle VAT matches HubSpot


@dataclass
class MatchSignal:
    """A single signal indicating a potential match."""
    signal_type: SignalType
    company: Company
    confidence: float  # 0.0 to 1.0
    source: str = ""  # Human-readable source description
    details: dict = field(default_factory=dict)


class SignalCollector:
    """
    Collects matching signals for an organization.
    
    Queries HubSpot and optionally Paddle to find potential company matches
    based on various signals.
    """
    
    def __init__(
        self,
        hubspot: HubSpotClient,
        config: Config,
        billing_computer: Optional[BillingStatusComputer] = None,
    ):
        """
        Initialize the signal collector.
        
        Args:
            hubspot: HubSpot API client
            config: Configuration
            billing_computer: Optional Paddle Billing API client
        """
        self.hubspot = hubspot
        self.config = config
        self.billing_computer = billing_computer
    
    def collect_signals(self, org: Organization) -> list[MatchSignal]:
        """
        Collect all matching signals for an organization.
        
        Args:
            org: Platform organization to find matches for
            
        Returns:
            List of MatchSignal objects, sorted by confidence descending
        """
        signals = []
        
        # 1. Check if a company already has this platform org ID (ground truth)
        existing_match = self._check_existing_platform_id(org)
        if existing_match:
            signals.append(existing_match)
        
        # 2. Check domain matching
        domain_signals = self._check_domain_matches(org)
        signals.extend(domain_signals)
        
        # 3. Check contact associations
        contact_signals = self._check_contact_associations(org)
        signals.extend(contact_signals)
        
        # 4. Check Paddle data if available
        if self.billing_computer and org.paddle_id:
            paddle_signals = self._check_paddle_matches(org)
            signals.extend(paddle_signals)
        
        # Sort by confidence descending
        signals.sort(key=lambda s: s.confidence, reverse=True)
        
        return signals
    
    def _check_existing_platform_id(self, org: Organization) -> Optional[MatchSignal]:
        """Check if a company already has this organization's platform ID."""
        company = self.hubspot.get_company_by_platform_org_id(org.id)
        if company:
            return MatchSignal(
                signal_type=SignalType.EXISTING_PLATFORM_ID,
                company=company,
                confidence=1.0,
                source=f"Company already has platform_org_id={org.id}",
                details={"already_linked": True},
            )
        return None
    
    def _check_domain_matches(self, org: Organization) -> list[MatchSignal]:
        """Check for companies with matching domains."""
        signals = []
        
        # Extract domains from org users (prefer admin)
        admin_email = org.admin_email
        domains_to_check = set()
        
        if admin_email:
            admin_domain = extract_domain(admin_email)
            if admin_domain and not is_generic_domain(admin_domain, self.config):
                domains_to_check.add(admin_domain)
        
        # Also check other user domains
        for email in org.user_emails:
            domain = extract_domain(email)
            if domain and not is_generic_domain(domain, self.config):
                domains_to_check.add(domain)
        
        # Search for companies with these domains
        seen_company_ids = set()
        for domain in domains_to_check:
            companies = self.hubspot.search_companies_by_domain(domain)
            for company in companies:
                if company.id in seen_company_ids:
                    continue
                seen_company_ids.add(company.id)
                
                # Higher confidence if domain matches admin email
                is_admin_domain = (
                    admin_email and 
                    extract_domain(admin_email) == domain
                )
                confidence = 0.85 if is_admin_domain else 0.7
                
                # Lower confidence if company already has a different platform ID
                if company.platform_org_id and company.platform_org_id != org.id:
                    confidence = 0.3  # Conflict situation
                
                signals.append(MatchSignal(
                    signal_type=SignalType.DOMAIN_MATCH,
                    company=company,
                    confidence=confidence,
                    source=f"Domain {domain} matches company {company.name}",
                    details={
                        "matched_domain": domain,
                        "is_admin_domain": is_admin_domain,
                        "existing_platform_id": company.platform_org_id,
                    },
                ))
        
        return signals
    
    def _check_contact_associations(self, org: Organization) -> list[MatchSignal]:
        """Check which companies have contacts associated that match org users."""
        signals = []
        
        # Find HubSpot contacts for org users
        contacts = self.hubspot.get_contacts_by_emails(org.user_emails)
        
        # Collect companies from contact associations
        company_counts: dict[str, dict] = {}  # company_id -> {company, count, emails}
        
        for contact in contacts:
            for company_id in contact.associated_company_ids:
                if company_id not in company_counts:
                    company = self.hubspot.get_company_by_id(company_id)
                    if company:
                        company_counts[company_id] = {
                            "company": company,
                            "count": 0,
                            "emails": [],
                        }
                if company_id in company_counts:
                    company_counts[company_id]["count"] += 1
                    company_counts[company_id]["emails"].append(contact.email)
        
        # Create signals for each company
        total_users = len(org.user_emails)
        for company_id, data in company_counts.items():
            company = data["company"]
            match_count = data["count"]
            emails = data["emails"]
            
            # Confidence based on proportion of users matched
            base_confidence = min(0.8, 0.4 + (match_count / max(total_users, 1)) * 0.4)
            
            # Lower confidence if company already has different platform ID
            if company.platform_org_id and company.platform_org_id != org.id:
                base_confidence = 0.2  # Conflict
            
            signals.append(MatchSignal(
                signal_type=SignalType.CONTACT_ASSOCIATION,
                company=company,
                confidence=base_confidence,
                source=f"{match_count}/{total_users} users associated with {company.name}",
                details={
                    "matched_count": match_count,
                    "total_users": total_users,
                    "matched_emails": emails,
                    "existing_platform_id": company.platform_org_id,
                },
            ))
        
        return signals
    
    def _check_paddle_matches(self, org: Organization) -> list[MatchSignal]:
        """Check for matches using Paddle customer data."""
        signals = []
        
        if not self.billing_computer or not org.paddle_id:
            return signals
        
        # Get Paddle customer info via Billing API
        try:
            paddle_info = self.billing_computer.get_customer_info(org.paddle_id)
        except Exception as e:
            print(f"  Warning: Could not fetch Paddle info for matching: {e}")
            return signals
        
        if not paddle_info:
            return signals
        
        # Search by company name if available
        paddle_name = paddle_info.get("name")
        if paddle_name:
            companies = self.hubspot.search_companies_by_name(paddle_name)
            for company in companies:
                confidence = 0.75
                if company.platform_org_id and company.platform_org_id != org.id:
                    confidence = 0.25
                
                signals.append(MatchSignal(
                    signal_type=SignalType.PADDLE_NAME_MATCH,
                    company=company,
                    confidence=confidence,
                    source=f"Paddle company name '{paddle_name}' matches {company.name}",
                    details={
                        "paddle_company_name": paddle_name,
                        "existing_platform_id": company.platform_org_id,
                    },
                ))
        
        return signals
