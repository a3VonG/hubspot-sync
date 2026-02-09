"""Tests for filter_config module."""

import pytest
from filter_config import (
    is_org_blacklisted,
    is_email_blacklisted,
    filter_emails,
    is_org_internal,
    BLACKLISTED_ORG_IDS,
    BLACKLISTED_EMAIL_DOMAINS,
)


class TestIsOrgBlacklisted:
    """Tests for is_org_blacklisted function."""
    
    def test_empty_blacklist(self):
        """Test with default empty blacklist."""
        # By default BLACKLISTED_ORG_IDS is empty, so nothing should be blacklisted
        if not BLACKLISTED_ORG_IDS:
            assert is_org_blacklisted("any-org-id") is False
    
    def test_blacklisted_org(self, monkeypatch):
        """Test that blacklisted orgs are detected."""
        import filter_config
        monkeypatch.setattr(filter_config, 'BLACKLISTED_ORG_IDS', {"org-123", "org-456"})
        
        assert filter_config.is_org_blacklisted("org-123") is True
        assert filter_config.is_org_blacklisted("org-456") is True
        assert filter_config.is_org_blacklisted("org-789") is False


class TestIsEmailBlacklisted:
    """Tests for is_email_blacklisted function."""
    
    def test_blacklisted_domain(self):
        """Test that emails from blacklisted domains are detected."""
        # relu.eu is blacklisted by default
        assert is_email_blacklisted("user@relu.eu") is True
        assert is_email_blacklisted("admin@relu.eu") is True
    
    def test_non_blacklisted_domain(self):
        """Test that emails from non-blacklisted domains pass."""
        assert is_email_blacklisted("user@company.com") is False
        assert is_email_blacklisted("admin@example.org") is False
    
    def test_case_insensitive(self):
        """Test that domain matching is case-insensitive."""
        assert is_email_blacklisted("user@RELU.EU") is True
        assert is_email_blacklisted("user@Relu.Eu") is True
    
    def test_empty_email(self):
        """Test handling of empty/None emails."""
        assert is_email_blacklisted("") is False
        assert is_email_blacklisted(None) is False
    
    def test_blacklisted_pattern(self, monkeypatch):
        """Test pattern matching."""
        import filter_config
        monkeypatch.setattr(filter_config, 'BLACKLISTED_EMAIL_PATTERNS', {"+test@", "noreply"})
        
        assert filter_config.is_email_blacklisted("user+test@company.com") is True
        assert filter_config.is_email_blacklisted("noreply@company.com") is True
        assert filter_config.is_email_blacklisted("user@company.com") is False


class TestFilterEmails:
    """Tests for filter_emails function."""
    
    def test_filter_blacklisted(self):
        """Test filtering blacklisted emails from a list."""
        emails = [
            "user@company.com",
            "admin@relu.eu",  # Blacklisted
            "another@example.org",
        ]
        result = filter_emails(emails)
        
        assert "user@company.com" in result
        assert "another@example.org" in result
        assert "admin@relu.eu" not in result
    
    def test_empty_list(self):
        """Test with empty list."""
        assert filter_emails([]) == []
    
    def test_all_blacklisted(self):
        """Test when all emails are blacklisted."""
        emails = ["user1@relu.eu", "user2@relu.eu"]
        result = filter_emails(emails)
        assert result == []


class TestIsOrgInternal:
    """Tests for is_org_internal function."""
    
    def test_admin_from_blacklisted_domain(self):
        """Test that orgs with blacklisted admin email are internal."""
        assert is_org_internal("admin@relu.eu") is True
        assert is_org_internal("admin@relu.eu", ["user@company.com"]) is True
    
    def test_all_users_blacklisted(self):
        """Test that orgs where all users are blacklisted are internal."""
        assert is_org_internal(
            "admin@company.com",  # Non-blacklisted admin
            ["user1@relu.eu", "user2@relu.eu"],  # All users blacklisted
        ) is True
    
    def test_mixed_users(self):
        """Test that orgs with mix of blacklisted and non-blacklisted are NOT internal."""
        assert is_org_internal(
            "admin@company.com",
            ["user1@relu.eu", "user2@company.com"],  # Mixed
        ) is False
    
    def test_normal_org(self):
        """Test that normal orgs are not internal."""
        assert is_org_internal("admin@company.com") is False
        assert is_org_internal("admin@company.com", ["user@example.org"]) is False
    
    def test_no_users(self):
        """Test with no users list."""
        assert is_org_internal("admin@company.com", None) is False
        assert is_org_internal("admin@company.com", []) is False
