"""
Tests for company creation and enrichment.
"""

import pytest
from unittest.mock import MagicMock

from hubspot_sync.actions.company_creator import (
    CompanyCreator, 
    CompanyCreateResult,
    SOURCE_AUTO_CREATED,
    SOURCE_ENRICHED,
)
from hubspot_sync.clients.platform import Organization, User
from hubspot_sync.clients.hubspot import Company
from hubspot_sync.utils.audit import AuditLog


class TestCompanyCreator:
    """Tests for the CompanyCreator class."""
    
    def test_create_placeholder_company(self, config, sample_organization):
        """Should create placeholder company with correct name format."""
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        hubspot.create_company.return_value = Company(
            id="new-company",
            name='[placeholder company from "admin@acme.com"]',
            domain="acme.com",
            platform_org_id=sample_organization.id,
        )
        
        config.dry_run = False
        config.auto_create_companies = True
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        creator = CompanyCreator(hubspot, config, audit_log)
        result = creator.create_or_enrich_company(sample_organization)
        
        assert result.success is True
        assert result.was_created is True
        assert result.company is not None
        
        # Check the company was created with correct properties
        hubspot.create_company.assert_called_once()
        call_args = hubspot.create_company.call_args[0][0]  # First positional arg (properties dict)
        assert '[placeholder company from "admin@acme.com"]' in call_args["name"]
        assert call_args["platform_org_id"] == sample_organization.id
        assert call_args[config.company_source_property] == SOURCE_AUTO_CREATED
    
    def test_create_placeholder_extracts_domain(self, config, sample_organization):
        """Should extract domain from admin email for placeholder."""
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        hubspot.create_company.return_value = Company(
            id="new-company",
            name="placeholder",
            domain="acme.com",
        )
        
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        creator = CompanyCreator(hubspot, config, audit_log)
        result = creator.create_or_enrich_company(sample_organization)
        
        call_args = hubspot.create_company.call_args[0][0]
        assert call_args.get("domain") == "acme.com"
    
    def test_skip_generic_domain_for_placeholder(self, config):
        """Should not set domain if admin email is generic."""
        org = Organization(
            id="org-generic",
            name="Generic Org",
            admin_user_id="user-1",
            users=[
                User(id="user-1", email="user@gmail.com", organization_id="org-generic"),
            ],
        )
        
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        hubspot.create_company.return_value = Company(id="new", name="placeholder")
        
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        creator = CompanyCreator(hubspot, config, audit_log)
        result = creator.create_or_enrich_company(org)
        
        call_args = hubspot.create_company.call_args[0][0]
        assert "domain" not in call_args  # Should not include gmail.com
    
    def test_enrich_placeholder_with_paddle(self, config, sample_organization):
        """Should enrich placeholder company when Paddle data available."""
        # Existing placeholder company
        existing_company = Company(
            id="existing",
            name='[placeholder company from "admin@acme.com"]',
            platform_org_id=sample_organization.id,
            properties={config.company_source_property: SOURCE_AUTO_CREATED},
        )
        
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = existing_company
        hubspot.get_company_with_source.return_value = existing_company
        hubspot.update_company.return_value = (True, "")
        
        # Mock billing computer with customer info
        billing_computer = MagicMock()
        billing_computer.get_customer_info.return_value = {
            "name": "Acme Corporation",
            "email": "billing@acme.com",
            "country_code": "US",
            "city": "San Francisco",
            "region": "California",
            "postal_code": "94105",
            "tax_identifier": None,
        }
        
        sample_organization.paddle_id = "paddle-123"
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        creator = CompanyCreator(hubspot, config, audit_log, billing_computer)
        result = creator.create_or_enrich_company(sample_organization)
        
        assert result.success is True
        assert result.was_enriched is True
        
        # Check update was called with enriched data
        hubspot.update_company.assert_called_once()
        call_args = hubspot.update_company.call_args
        properties = call_args[0][1]  # Second positional arg
        assert properties["name"] == "Acme Corporation"
        assert properties["country"] == "US"
        assert properties["city"] == "San Francisco"
        assert properties["state"] == "California"
        assert properties["zip"] == "94105"
        assert properties[config.company_source_property] == SOURCE_ENRICHED
    
    def test_no_enrich_manual_company(self, config, sample_organization):
        """Should not enrich company that wasn't auto-created."""
        # Existing manual company (no source or source=manual)
        existing_company = Company(
            id="existing",
            name="Acme Inc",
            platform_org_id=sample_organization.id,
            properties={},  # No source property = manual
        )
        
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = existing_company
        hubspot.get_company_with_source.return_value = existing_company
        
        billing_computer = MagicMock()
        billing_computer.get_customer_info.return_value = {
            "name": "Different Name",
            "email": None,
            "country_code": None,
            "city": None,
            "region": None,
            "postal_code": None,
            "tax_identifier": None,
        }
        
        sample_organization.paddle_id = "paddle-123"
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        creator = CompanyCreator(hubspot, config, audit_log, billing_computer)
        result = creator.create_or_enrich_company(sample_organization)
        
        assert result.success is True
        assert result.was_enriched is False
        # Should NOT have called update
        hubspot.update_company.assert_not_called()
    
    def test_dry_run_creates_nothing(self, config, sample_organization):
        """Should not create company in dry run mode."""
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        
        config.dry_run = True
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        creator = CompanyCreator(hubspot, config, audit_log)
        result = creator.create_or_enrich_company(sample_organization)
        
        assert result.success is True
        assert result.was_created is True
        assert "[DRY RUN]" in result.message
        hubspot.create_company.assert_not_called()
