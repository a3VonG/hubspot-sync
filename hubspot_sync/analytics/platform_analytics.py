"""
Main analytics computer that orchestrates all metric calculations.
"""

from typing import Optional

from psycopg2.extras import RealDictCursor

from .models import OrganizationAnalytics
from .usage_metrics import UsageMetricsComputer
from .order_metrics import OrderMetricsComputer
from .account_metrics import AccountMetricsComputer
from .billing_status import BillingStatusComputer, BillingStatus
from ..config import Config, DatabaseConfig
from ..utils.database import DatabaseConnection


class PlatformAnalyticsComputer:
    """
    Computes all platform analytics for organizations.
    
    Orchestrates the individual metric computers and combines results
    into a unified OrganizationAnalytics object.
    """
    
    def __init__(
        self,
        db_config: DatabaseConfig,
        config: Config,
        paddle_vendor_id: Optional[str] = None,
        paddle_api_key: Optional[str] = None,
    ):
        """
        Initialize the analytics computer.
        
        Args:
            db_config: Database configuration
            config: Configuration
            paddle_vendor_id: Paddle vendor ID for billing status
            paddle_api_key: Paddle API key for billing status
        """
        self.db_config = db_config
        self.config = config
        self._db: Optional[DatabaseConnection] = None
        
        # Initialize billing status computer if Paddle credentials provided
        self.billing_computer = None
        if paddle_vendor_id and paddle_api_key:
            self.billing_computer = BillingStatusComputer(paddle_vendor_id, paddle_api_key)
    
    def _get_connection(self):
        """Get or create database connection."""
        if self._db is None:
            self._db = DatabaseConnection(self.db_config)
            self._db.connect()
        return self._db.connection
    
    def close(self):
        """Close database connection."""
        if self._db:
            self._db.close()
            self._db = None
    
    def compute_for_organization(
        self,
        org_id: str,
        paddle_id: Optional[str] = None,
    ) -> OrganizationAnalytics:
        """
        Compute all analytics for a single organization.
        
        Args:
            org_id: Organization UUID
            paddle_id: Optional Paddle ID for billing status
            
        Returns:
            OrganizationAnalytics with all computed metrics
        """
        conn = self._get_connection()
        
        # Initialize metric computers
        usage_computer = UsageMetricsComputer(conn)
        order_computer = OrderMetricsComputer(conn)
        account_computer = AccountMetricsComputer(conn)
        
        # Compute individual metrics
        usage_metrics = usage_computer.compute_for_organization(org_id)
        order_metrics = order_computer.compute_for_organization(org_id)
        account_metrics = account_computer.compute_for_organization(org_id)
        
        # Get billing status
        billing_status = BillingStatus()
        if self.billing_computer and paddle_id:
            billing_status = self.billing_computer.get_billing_status(paddle_id)
        
        # Determine testing status
        testing_status = self._determine_testing_status(
            account_metrics, billing_status, usage_metrics.has_used_product
        )
        
        # Build combined analytics
        return OrganizationAnalytics(
            organization_id=org_id,
            
            # Account
            admin_email=account_metrics.admin_email,
            has_account=True,
            organization_accounts=account_metrics.user_count,
            signed_up_date=usage_metrics.signed_up_date,
            paddle_customer_id=paddle_id,
            
            # Billing
            billing_status=billing_status.status,
            testing_status=testing_status,
            
            # Usage
            has_used_product=usage_metrics.has_used_product,
            last_usage_date=usage_metrics.last_usage_date,
            usage_last_7_days=usage_metrics.usage_last_7_days,
            usage_last_30_days=usage_metrics.usage_last_30_days,
            usage_trend=usage_metrics.usage_trend,
            
            # Services (formatted with counts)
            services_used_last_30_days=order_metrics.format_services_with_counts(order_metrics.services_used_last_30_days),
            
            # Testing metrics
            testing_free_credits_remaining=account_metrics.free_credits_remaining,
            testing_services_used=order_metrics.format_services_with_counts(order_metrics.services_used_all_time),
            testing_successful_cases=order_metrics.successful_cases,
            testing_failed_cases=order_metrics.failed_cases,
            
            # Issues
            number_errors_last_30_days=order_metrics.errors_last_30_days,
            refunds_and_feedback_last_30_days=order_metrics.refunds_and_feedback_last_30_days,
        )
    
    def compute_for_organizations_batch(
        self,
        organizations: list[dict],
    ) -> dict[str, OrganizationAnalytics]:
        """
        Compute analytics for multiple organizations efficiently.
        
        Args:
            organizations: List of dicts with 'id' and optional 'paddle_id'
            
        Returns:
            Dictionary mapping org_id to OrganizationAnalytics
        """
        if not organizations:
            return {}
        
        conn = self._get_connection()
        
        org_ids = [org["id"] for org in organizations]
        paddle_id_map = {org["id"]: org.get("paddle_id") for org in organizations}
        
        # Initialize metric computers
        usage_computer = UsageMetricsComputer(conn)
        order_computer = OrderMetricsComputer(conn)
        account_computer = AccountMetricsComputer(conn)
        
        # Batch compute metrics
        print(f"  Computing usage metrics for {len(org_ids)} organizations...")
        usage_metrics = usage_computer.compute_for_organizations_batch(org_ids)
        
        print(f"  Computing order metrics...")
        order_metrics = order_computer.compute_for_organizations_batch(org_ids)
        
        print(f"  Computing account metrics...")
        account_metrics = account_computer.compute_for_organizations_batch(org_ids)
        
        # Get billing status - batch fetch for all paddle IDs at once
        billing_statuses = {}
        if self.billing_computer:
            # Collect all paddle IDs that exist
            paddle_ids_to_check = [
                paddle_id_map[org_id]
                for org_id in org_ids
                if paddle_id_map.get(org_id)
            ]
            
            if paddle_ids_to_check:
                print(f"  Fetching billing status for {len(paddle_ids_to_check)} Paddle customers...")
                paddle_status_map = self.billing_computer.get_billing_status_batch(paddle_ids_to_check)
                
                # Map back to org_ids
                for org_id in org_ids:
                    paddle_id = paddle_id_map.get(org_id)
                    if paddle_id and paddle_id in paddle_status_map:
                        billing_statuses[org_id] = paddle_status_map[paddle_id]
                    else:
                        billing_statuses[org_id] = BillingStatus()
            else:
                print(f"  No Paddle IDs to check")
                for org_id in org_ids:
                    billing_statuses[org_id] = BillingStatus()
        
        # Build combined analytics for each org
        results = {}
        for org_id in org_ids:
            usage = usage_metrics.get(org_id, None)
            orders = order_metrics.get(org_id, None)
            account = account_metrics.get(org_id, None)
            billing = billing_statuses.get(org_id, BillingStatus())
            
            if not usage:
                from analytics.usage_metrics import UsageMetrics
                usage = UsageMetrics()
            if not orders:
                from analytics.order_metrics import OrderMetrics
                orders = OrderMetrics()
            if not account:
                from analytics.account_metrics import AccountMetrics
                account = AccountMetrics()
            
            testing_status = self._determine_testing_status(
                account, billing, usage.has_used_product
            )
            
            results[org_id] = OrganizationAnalytics(
                organization_id=org_id,
                
                # Account
                admin_email=account.admin_email,
                has_account=True,
                organization_accounts=account.user_count,
                signed_up_date=usage.signed_up_date,
                paddle_customer_id=paddle_id_map.get(org_id),
                
                # Billing
                billing_status=billing.status,
                testing_status=testing_status,
                
                # Usage
                has_used_product=usage.has_used_product,
                last_usage_date=usage.last_usage_date,
                usage_last_7_days=usage.usage_last_7_days,
                usage_last_30_days=usage.usage_last_30_days,
                usage_trend=usage.usage_trend,
                
                # Services (formatted with counts)
                services_used_last_30_days=orders.format_services_with_counts(orders.services_used_last_30_days),
                
                # Testing metrics
                testing_free_credits_remaining=account.free_credits_remaining,
                testing_services_used=orders.format_services_with_counts(orders.services_used_all_time),
                testing_successful_cases=orders.successful_cases,
                testing_failed_cases=orders.failed_cases,
                
                # Issues
                number_errors_last_30_days=orders.errors_last_30_days,
                refunds_and_feedback_last_30_days=orders.refunds_and_feedback_last_30_days,
            )
        
        return results
    
    def _determine_testing_status(
        self,
        account_metrics,
        billing_status: BillingStatus,
        has_used_product: bool = False,
    ) -> str:
        """
        Determine the testing status of an organization.
        
        Returns:
            "account_created" - no subscription AND hasn't used product (fresh signup)
            "testing"         - no subscription AND has used product (uploaded something)
            "not_testing"     - has (or had) a Paddle subscription
        """
        # Check if org is in a non-paying state (no billing or fresh signup)
        is_non_paying = account_metrics.has_no_billing_scope or billing_status.is_testing
        
        if not is_non_paying:
            return "not_testing"
        
        # Within the non-paying state, distinguish account_created vs testing
        if has_used_product:
            return "testing"
        return "account_created"
    
