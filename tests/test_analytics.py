"""Tests for the analytics module."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from analytics.models import OrganizationAnalytics
from analytics.usage_metrics import UsageMetrics, UsageMetricsComputer
from analytics.order_metrics import OrderMetrics, OrderMetricsComputer
from analytics.account_metrics import AccountMetrics, AccountMetricsComputer
from analytics.billing_status import BillingStatus, BillingStatusComputer
from analytics.platform_analytics import PlatformAnalyticsComputer


class TestOrganizationAnalytics:
    """Tests for OrganizationAnalytics dataclass."""
    
    def test_to_hubspot_properties_basic(self):
        """Test conversion to HubSpot properties."""
        analytics = OrganizationAnalytics(
            organization_id="org-123",
            admin_email="admin@example.com",
            has_account=True,
            organization_accounts=5,
            billing_status="active",
            usage_last_7_days=100.0,
            usage_last_30_days=500.0,
            usage_trend="up",
        )
        
        props = analytics.to_hubspot_properties()
        
        assert props["platform_organization_id"] == "org-123"
        assert props["platform_admin_email"] == "admin@example.com"
        assert props["platform_has_account"] == "true"
        assert props["platform_organisation_accounts"] == "5"
        assert props["platform_billing_active"] == "active"
        assert props["platform_usage_last_7_days"] == "100.0"
        assert props["platform_usage_last_30_days"] == "500.0"
        assert props["platform_usage_trend"] == "up"
    
    def test_to_hubspot_properties_with_dates(self):
        """Test date conversion to HubSpot milliseconds format."""
        signed_up = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        last_usage = datetime(2025, 2, 1, 14, 0, 0, tzinfo=timezone.utc)
        
        analytics = OrganizationAnalytics(
            organization_id="org-123",
            signed_up_date=signed_up,
            last_usage_date=last_usage,
        )
        
        props = analytics.to_hubspot_properties()
        
        # Should be timestamp at midnight UTC in milliseconds
        assert "platform_signed_up_date" in props
        assert "platform_last_usage_date" in props
        
        # Verify they're integers (milliseconds)
        assert int(props["platform_signed_up_date"]) > 0
        assert int(props["platform_last_usage_date"]) > 0
    
    def test_to_hubspot_properties_empty_values(self):
        """Test handling of empty/None values."""
        analytics = OrganizationAnalytics(
            organization_id="org-123",
            admin_email=None,
            services_used_last_30_days="",
        )
        
        props = analytics.to_hubspot_properties()
        
        assert props["platform_admin_email"] == ""
        assert props["platform_services_used"] == ""


class TestUsageMetrics:
    """Tests for UsageMetricsComputer."""
    
    def test_calculate_trend_stable(self):
        """Test trend calculation for stable usage."""
        conn = MagicMock()
        computer = UsageMetricsComputer(conn)
        
        # Within 10% change = stable
        assert computer._calculate_trend(100, 95) == "stable"
        assert computer._calculate_trend(100, 105) == "stable"
    
    def test_calculate_trend_increasing(self):
        """Test trend calculation for increasing usage."""
        conn = MagicMock()
        computer = UsageMetricsComputer(conn)
        
        assert computer._calculate_trend(150, 100) == "up"
        assert computer._calculate_trend(100, 0) == "up"
    
    def test_calculate_trend_decreasing(self):
        """Test trend calculation for decreasing usage."""
        conn = MagicMock()
        computer = UsageMetricsComputer(conn)
        
        assert computer._calculate_trend(80, 100) == "down"
        assert computer._calculate_trend(50, 100) == "down"
    
    def test_calculate_trend_both_zero(self):
        """Test trend calculation when both periods are zero."""
        conn = MagicMock()
        computer = UsageMetricsComputer(conn)
        
        assert computer._calculate_trend(0, 0) == "stable"


class TestBillingStatus:
    """Tests for BillingStatus."""
    
    def test_status_not_started(self):
        """Test billing status when no subscription exists."""
        status = BillingStatus(
            has_active_subscription=False,
            has_any_subscription_history=False,
        )
        assert status.status == "not started"
        assert status.is_testing is True
    
    def test_status_active(self):
        """Test billing status with active subscription."""
        status = BillingStatus(
            has_active_subscription=True,
            has_any_subscription_history=True,
        )
        assert status.status == "active"
        assert status.is_testing is False
    
    def test_status_cancelled(self):
        """Test billing status with cancelled subscription (has history)."""
        status = BillingStatus(
            has_active_subscription=False,
            has_any_subscription_history=True,  # Had a subscription before
        )
        assert status.status == "cancelled"
        assert status.is_testing is False  # Not testing - they're a churned customer


class TestBillingStatusComputer:
    """Tests for BillingStatusComputer batch functionality."""
    
    def test_get_billing_status_batch_filters_empty_ids(self):
        """Test that empty/None paddle IDs are handled correctly."""
        computer = BillingStatusComputer("vendor123", "api_key")
        
        # Mock the session to avoid actual API calls
        with patch.object(computer.session, 'get') as mock_get:
            mock_get.return_value.json.return_value = {"data": []}
            mock_get.return_value.raise_for_status = MagicMock()
            
            result = computer.get_billing_status_batch(["", None, "valid_id"])
            
            # Empty/None IDs should get default BillingStatus
            assert result[""] == BillingStatus()
            assert result[None] == BillingStatus()
    
    def test_get_billing_status_batch_processes_results(self):
        """Test batch processing of subscription results."""
        computer = BillingStatusComputer("vendor123", "api_key")
        
        with patch.object(computer.session, 'get') as mock_get:
            mock_get.return_value.json.return_value = {
                "data": [
                    {"customer_id": "cust_1", "status": "active"},
                    {"customer_id": "cust_2", "status": "canceled"},
                ],
                "meta": {"pagination": {"has_more": False}},
            }
            mock_get.return_value.raise_for_status = MagicMock()
            
            result = computer.get_billing_status_batch(["cust_1", "cust_2", "cust_3"])
            
            # cust_1 has active subscription
            assert result["cust_1"].has_active_subscription is True
            assert result["cust_1"].has_any_subscription_history is True
            
            # cust_2 has cancelled subscription (history but not active)
            assert result["cust_2"].has_active_subscription is False
            assert result["cust_2"].has_any_subscription_history is True
            
            # cust_3 has no subscription data
            assert result["cust_3"].has_active_subscription is False
            assert result["cust_3"].has_any_subscription_history is False


class TestAccountMetrics:
    """Tests for AccountMetrics."""
    
    def test_has_no_billing_scope(self):
        """Test NO_BILLING scope detection."""
        metrics = AccountMetrics(scopes=["NO_BILLING", "SOME_OTHER_SCOPE"])
        assert metrics.has_no_billing_scope is True
        
        metrics = AccountMetrics(scopes=["SOME_SCOPE"])
        assert metrics.has_no_billing_scope is False
        
        metrics = AccountMetrics(scopes=[])
        assert metrics.has_no_billing_scope is False


class TestPlatformAnalyticsComputer:
    """Tests for PlatformAnalyticsComputer."""
    
    @staticmethod
    def _make_test_config():
        """Create a test config with mock database config."""
        from config import Config, DatabaseConfig
        db_config = DatabaseConfig(
            host="localhost",
            port=5432,
            name="test",
            user="test",
            password="test",
        )
        return Config(
            hubspot_api_key="test",
            db_config=db_config,
        ), db_config
    
    def test_determine_testing_status_no_billing_scope(self):
        """Test testing status with NO_BILLING scope."""
        with patch('analytics.platform_analytics.DatabaseConnection'):
            config, db_config = self._make_test_config()
            computer = PlatformAnalyticsComputer(db_config, config)
            
            account = AccountMetrics(scopes=["NO_BILLING"])
            billing = BillingStatus(has_active_subscription=True)  # Even with active sub
            
            # NO_BILLING scope overrides everything
            assert computer._determine_testing_status(account, billing) is True
    
    def test_determine_testing_status_fresh_signup(self):
        """Test testing status for fresh signup."""
        with patch('analytics.platform_analytics.DatabaseConnection'):
            config, db_config = self._make_test_config()
            computer = PlatformAnalyticsComputer(db_config, config)
            
            account = AccountMetrics(scopes=[])
            billing = BillingStatus(
                has_active_subscription=False,
                has_any_subscription_history=False,
            )
            
            assert computer._determine_testing_status(account, billing) is True
    
    def test_determine_testing_status_production(self):
        """Test testing status for production customer."""
        with patch('analytics.platform_analytics.DatabaseConnection'):
            config, db_config = self._make_test_config()
            computer = PlatformAnalyticsComputer(db_config, config)
            
            account = AccountMetrics(scopes=[])
            billing = BillingStatus(
                has_active_subscription=True,
                has_any_subscription_history=True,
            )
            
            assert computer._determine_testing_status(account, billing) is False
