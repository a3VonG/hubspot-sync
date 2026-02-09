"""
Pytest configuration and shared fixtures.
"""

import pytest
from unittest.mock import MagicMock, patch

from config import Config, DatabaseConfig
from clients.platform import Organization, User
from clients.hubspot import Company, Contact


@pytest.fixture
def db_config():
    """Create a test database configuration."""
    return DatabaseConfig(
        host="localhost",
        port=5432,
        name="test",
        user="test",
        password="test",
    )


@pytest.fixture
def config(db_config):
    """Create a test configuration."""
    return Config(
        hubspot_api_key="test-api-key",
        db_config=db_config,
        hubspot_platform_org_id_property="platform_org_id",
        paddle_api_key=None,
        paddle_vendor_id=None,
        slack_webhook_url=None,
        auto_link_confidence_threshold=0.8,
        dry_run=True,
    )


@pytest.fixture
def sample_organization():
    """Create a sample organization for testing."""
    return Organization(
        id="org-123",
        name="Acme Corp",
        admin_user_id="user-1",
        paddle_id="paddle-123",
        users=[
            User(
                id="user-1",
                email="admin@acme.com",
                organization_id="org-123",
                first_name="John",
                last_name="Admin",
            ),
            User(
                id="user-2",
                email="user@acme.com",
                organization_id="org-123",
                first_name="Jane",
                last_name="User",
            ),
        ],
    )


@pytest.fixture
def sample_company():
    """Create a sample HubSpot company for testing."""
    return Company(
        id="hs-company-1",
        name="Acme Corporation",
        domain="acme.com",
        platform_org_id=None,
    )


@pytest.fixture
def sample_company_with_platform_id():
    """Create a sample HubSpot company that's already linked."""
    return Company(
        id="hs-company-2",
        name="Linked Corp",
        domain="linked.com",
        platform_org_id="org-123",
    )


@pytest.fixture
def sample_contact():
    """Create a sample HubSpot contact for testing."""
    return Contact(
        id="hs-contact-1",
        email="admin@acme.com",
        firstname="John",
        lastname="Admin",
        associated_company_ids=["hs-company-1"],
    )


@pytest.fixture
def mock_hubspot_client(sample_company, sample_contact):
    """Create a mock HubSpot client."""
    mock = MagicMock()
    mock.platform_org_id_property = "platform_org_id"
    
    # Default return values
    mock.get_company_by_platform_org_id.return_value = None
    mock.search_companies_by_domain.return_value = [sample_company]
    mock.get_contact_by_email.return_value = sample_contact
    mock.get_contacts_by_emails.return_value = [sample_contact]
    mock.get_company_by_id.return_value = sample_company
    mock.update_company_platform_org_id.return_value = True
    mock.create_contact.return_value = sample_contact
    mock.associate_contact_with_company.return_value = True
    mock.create_task.return_value = MagicMock(id="task-1", subject="Test Task")
    mock.search_tasks_by_subject.return_value = []
    
    return mock


@pytest.fixture
def mock_platform_client(sample_organization):
    """Create a mock platform client."""
    mock = MagicMock()
    mock.get_all_organizations.return_value = [sample_organization]
    mock.get_organization_by_id.return_value = sample_organization
    return mock
