"""
Data models for organization analytics.

This module defines the OrganizationAnalytics dataclass which represents
all analytics properties that can be synced to HubSpot.

Property Documentation: See ANALYTICS.md for detailed definitions.

Property Categories:
    - Core: Always synced (billing, usage, account info)
    - Testing: Only synced when testing_status="testing"
    - Issues: Error and feedback counts

Business Logic:
    - testing_status: "account_created", "testing", or "not_testing"
      - "account_created": no subscription AND has NOT used product (fresh signup)
      - "testing": no subscription AND HAS used product (uploaded something)
      - "not_testing": has (or had) a Paddle subscription
    - billing_status: "not_started", "active", or "cancelled"
    - usage_trend: Compares last 30 days vs previous 30 days (±10% threshold)
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class OrganizationAnalytics:
    """
    Analytics data for a single organization.
    
    Maps to HubSpot company properties with 'platform_' prefix.
    Testing-specific fields are conditionally included based on testing_status.
    
    Attributes:
        organization_id: Platform org UUID (links to HubSpot company)
        
        # --- ACCOUNT INFO (Core) ---
        admin_email: Email of org admin user
        has_account: Always True if org exists in platform
        organization_accounts: Count of users in org
        signed_up_date: Date of first GIFT_TOPUP (welcome bonus)
        paddle_customer_id: Paddle customer ID (ctm_...) for billing lookups
        
        # --- BILLING (Core) ---
        billing_status: "not_started", "active", or "cancelled"
        testing_status: "account_created", "testing", or "not_testing"
        
        # --- USAGE METRICS (Core) ---
        has_used_product: True if org has at least one ORDER_USAGE transaction
        last_usage_date: Most recent usage_transaction date
        usage_last_7_days: Sum of ORDER_USAGE credits (7 days)
        usage_last_30_days: Sum of ORDER_USAGE credits (30 days)
        usage_trend: 'up'|'stable'|'down' based on 30d comparison
        services_used_last_30_days: "Service A (45), Service B (12)" format
        
        # --- TESTING METRICS (Conditional: testing_status="testing" only) ---
        testing_free_credits_remaining: Negated org.usage (negative = overdrawn)
        testing_services_used: All-time services used during trial
        testing_successful_cases: Jobs with status='Done' (all time)
        testing_failed_cases: Jobs with status='Failed' (all time)
        
        # --- ISSUES (Core) ---
        number_errors_last_30_days: Failed jobs in last 30 days
        refunds_and_feedback_last_30_days: Feedback entries (30 days)
    """
    organization_id: str
    
    # --------------------------------------------------------------------------
    # ACCOUNT INFO (always synced)
    # --------------------------------------------------------------------------
    admin_email: Optional[str] = None
    has_account: bool = True
    organization_accounts: int = 0
    signed_up_date: Optional[datetime] = None
    paddle_customer_id: Optional[str] = None  # Paddle customer ID (ctm_...)
    
    # --------------------------------------------------------------------------
    # BILLING (always synced)
    # "not_started" = no subscription, "active" = active sub, "cancelled" = had sub
    # --------------------------------------------------------------------------
    billing_status: str = "not started"
    # "account_created" = no sub + hasn't used product
    # "testing" = no sub + has used product (uploaded something)
    # "not_testing" = has (or had) a subscription
    testing_status: str = "account_created"
    
    # --------------------------------------------------------------------------
    # USAGE METRICS (always synced)
    # Source: usage_transactions table (type=ORDER_USAGE)
    # --------------------------------------------------------------------------
    has_used_product: bool = False  # At least one ORDER_USAGE transaction (all-time)
    last_usage_date: Optional[datetime] = None
    usage_last_7_days: float = 0.0
    usage_last_30_days: float = 0.0
    usage_trend: str = "stable"  # up (>10%), down (<-10%), stable
    
    # --------------------------------------------------------------------------
    # SERVICE USAGE (always synced)
    # Format: "Service Name (count), Service Name (count)"
    # --------------------------------------------------------------------------
    services_used_last_30_days: str = ""
    
    # --------------------------------------------------------------------------
    # TESTING METRICS (only synced when testing_status="testing")
    # These are irrelevant for paying customers or fresh signups with no usage
    # --------------------------------------------------------------------------
    testing_free_credits_remaining: float = 0.0
    testing_services_used: str = ""
    testing_successful_cases: int = 0
    testing_failed_cases: int = 0
    
    # --------------------------------------------------------------------------
    # ISSUES (always synced)
    # Source: jobs (failures), feedback (refunds/complaints)
    # --------------------------------------------------------------------------
    number_errors_last_30_days: int = 0
    refunds_and_feedback_last_30_days: int = 0
    
    def to_hubspot_properties(self) -> dict:
        """
        Convert to HubSpot company properties.
        
        Property names use underscore format (e.g., platform_admin_email).
        Testing-specific fields are only included when testing_status="testing"
        (i.e. the org has actually used the product but has no subscription).
        """
        # Core properties (always included)
        props = {
            "platform_organization_id": self.organization_id,
            "platform_admin_email": self.admin_email or "",
            "platform_has_account": "true" if self.has_account else "false",
            "platform_organisation_accounts": str(self.organization_accounts),
            "platform_billing_active": self.billing_status,
            "platform_testing_status": self.testing_status,
            "platform_has_used_prodcut": "true" if self.has_used_product else "false",
            "platform_usage_last_7_days": str(self.usage_last_7_days),
            "platform_usage_last_30_days": str(self.usage_last_30_days),
            "platform_usage_trend": self.usage_trend,
            "platform_services_used": self.services_used_last_30_days,
            "platform_number_errors_last_30_days": str(self.number_errors_last_30_days),
            "platform_refunds_last_30_days": str(self.refunds_and_feedback_last_30_days),
            # Sync timestamp (HubSpot "Date and time picker" expects ms since epoch)
            "platform_last_sync_analytics_time": str(
                int(datetime.now(timezone.utc).timestamp() * 1000)
            ),
        }
        
        # Paddle customer ID (only set if available)
        if self.paddle_customer_id:
            props["platform_paddle_customer_id"] = self.paddle_customer_id
        
        # Metabase dashboard link (parameterized by org ID)
        props["metabase_dashboard"] = (
            f"https://relu.metabaseapp.com/public/dashboard/"
            f"d0d0dd10-7153-464e-8dc7-d8a0fc5c827e?organisation={self.organization_id}"
        )
        
        # Testing-specific properties (only for orgs actively testing - have used product)
        if self.testing_status == "testing":
            props["platform_free_credits_remaining"] = str(self.testing_free_credits_remaining)
            props["platform_testing_services_used"] = self.testing_services_used
            props["platform_testing_succesful_cases"] = str(self.testing_successful_cases)
            props["platform_testing_failed_cases"] = str(self.testing_failed_cases)
        
        # Date fields - format as human-readable date strings
        if self.signed_up_date:
            props["platform_signed_up_date"] = self.signed_up_date.strftime("%Y-%m-%d")
        
        if self.last_usage_date:
            props["platform_last_usage_date"] = self.last_usage_date.strftime("%Y-%m-%d")
        
        return props
