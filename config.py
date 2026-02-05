"""
Configuration for HubSpot-Platform Sync.

Environment variables:
- HUBSPOT_API_KEY: HubSpot private app access token
- PLATFORM_DB_URL: PostgreSQL connection string for platform database
- PADDLE_API_KEY: Paddle API key (optional)
- PADDLE_VENDOR_ID: Paddle vendor ID (optional)
- SLACK_WEBHOOK_URL: Slack webhook for reports (optional)
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    """Configuration container for the sync system."""
    
    # HubSpot (required)
    hubspot_api_key: str
    
    # Platform Database (required)
    platform_db_url: str
    
    # HubSpot property name (optional with default)
    hubspot_platform_org_id_property: str = "platform_org_id"
    
    # Paddle (optional)
    paddle_api_key: Optional[str] = None
    paddle_vendor_id: Optional[str] = None
    
    # Slack (optional)
    slack_webhook_url: Optional[str] = None
    
    # Matching configuration
    auto_link_confidence_threshold: float = 0.8
    
    # Auto-create companies for unmatched organizations
    auto_create_companies: bool = False
    
    # Property names for tracking company source
    company_source_property: str = "platform_company_source"
    
    # Generic email domains to skip for domain matching
    generic_email_domains: tuple = (
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "yahoo.co.uk",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "gmx.com",
        "gmx.de",
        "web.de",
        "t-online.de",
    )
    
    # Dry run mode - if True, don't make changes to HubSpot
    dry_run: bool = False
    
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            hubspot_api_key=os.environ["HUBSPOT_API_KEY"],
            hubspot_platform_org_id_property=os.environ.get(
                "HUBSPOT_PLATFORM_ORG_ID_PROPERTY", "platform_org_id"
            ),
            platform_db_url=os.environ["PLATFORM_DB_URL"],
            paddle_api_key=os.environ.get("PADDLE_API_KEY"),
            paddle_vendor_id=os.environ.get("PADDLE_VENDOR_ID"),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL"),
            auto_link_confidence_threshold=float(
                os.environ.get("AUTO_LINK_CONFIDENCE_THRESHOLD", "0.8")
            ),
            auto_create_companies=os.environ.get("AUTO_CREATE_COMPANIES", "false").lower() == "true",
            company_source_property=os.environ.get("COMPANY_SOURCE_PROPERTY", "platform_company_source"),
            dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
        )
