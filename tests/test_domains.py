"""
Tests for domain extraction utilities.
"""

import pytest

from utils.domains import extract_domain, is_generic_domain, get_organization_domains
from config import Config


class TestExtractDomain:
    """Tests for extract_domain function."""
    
    def test_extract_valid_domain(self):
        """Should extract domain from valid email."""
        assert extract_domain("user@example.com") == "example.com"
        assert extract_domain("admin@acme.co.uk") == "acme.co.uk"
    
    def test_extract_domain_uppercase(self):
        """Should lowercase domain."""
        assert extract_domain("User@EXAMPLE.COM") == "example.com"
    
    def test_extract_domain_with_spaces(self):
        """Should handle whitespace."""
        assert extract_domain("user@example.com ") == "example.com"
    
    def test_extract_domain_invalid_email(self):
        """Should return None for invalid emails."""
        assert extract_domain("invalid-email") is None
        assert extract_domain("") is None
        assert extract_domain(None) is None
    
    def test_extract_domain_empty_domain(self):
        """Should return None for empty domain."""
        assert extract_domain("user@") is None


class TestIsGenericDomain:
    """Tests for is_generic_domain function."""
    
    def test_generic_domains(self):
        """Should identify generic email domains."""
        assert is_generic_domain("gmail.com") is True
        assert is_generic_domain("outlook.com") is True
        assert is_generic_domain("yahoo.com") is True
        assert is_generic_domain("hotmail.com") is True
        assert is_generic_domain("icloud.com") is True
    
    def test_non_generic_domains(self):
        """Should identify company domains as non-generic."""
        assert is_generic_domain("acme.com") is False
        assert is_generic_domain("company.io") is False
        assert is_generic_domain("startup.co") is False
    
    def test_case_insensitive(self):
        """Should be case insensitive."""
        assert is_generic_domain("GMAIL.COM") is True
        assert is_generic_domain("Gmail.Com") is True
    
    def test_empty_domain(self):
        """Should treat empty domain as generic."""
        assert is_generic_domain("") is True
        assert is_generic_domain(None) is True
    
    def test_with_custom_config(self):
        """Should use config's generic domains list."""
        from config import DatabaseConfig
        db_config = DatabaseConfig(
            host="localhost", port=5432, name="test", user="test", password="test"
        )
        config = Config(
            hubspot_api_key="test",
            db_config=db_config,
            generic_email_domains=("custom.com",),
        )
        assert is_generic_domain("custom.com", config) is True
        assert is_generic_domain("gmail.com", config) is False  # Not in custom list


class TestGetOrganizationDomains:
    """Tests for get_organization_domains function."""
    
    def test_extract_unique_domains(self):
        """Should extract unique non-generic domains."""
        emails = [
            "user1@company.com",
            "user2@company.com",
            "user3@other.com",
            "user4@gmail.com",  # Generic, should be excluded
        ]
        domains = get_organization_domains(emails)
        assert domains == {"company.com", "other.com"}
    
    def test_empty_list(self):
        """Should handle empty email list."""
        assert get_organization_domains([]) == set()
    
    def test_all_generic(self):
        """Should return empty set if all emails are generic."""
        emails = ["user@gmail.com", "other@yahoo.com"]
        assert get_organization_domains(emails) == set()
    
    def test_invalid_emails(self):
        """Should skip invalid emails."""
        emails = ["valid@company.com", "invalid-email", "", None]
        domains = get_organization_domains(emails)
        assert domains == {"company.com"}
