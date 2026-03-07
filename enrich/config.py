"""Configuration for the enrich module.

Environment variables
---------------------
ANTHROPIC_API_KEY   : Required.  Anthropic API key for Claude.
ENRICH_MODEL        : Optional.  Model name (default: claude-sonnet-4-20250514).
CLAY_WEBHOOK_URL    : Optional.  Clay table webhook URL (Clay disabled when absent).
CLAY_API_KEY        : Optional.  Clay API key.
ENRICH_MAX_PAGES    : Optional.  Max sub-pages to scrape per site (default: 4).
ENRICH_TIMEOUT      : Optional.  HTTP timeout in seconds (default: 15).
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class EnrichConfig:
    """All settings the enrich module needs."""

    anthropic_api_key: str
    model: str = "claude-sonnet-4-20250514"

    # Clay (optional -- module works fully without it)
    clay_webhook_url: Optional[str] = None
    clay_api_key: Optional[str] = None

    # Scraper tuning
    max_pages_per_site: int = 4
    timeout: int = 15

    @property
    def clay_enabled(self) -> bool:
        """True when Clay is configured and available."""
        return bool(self.clay_webhook_url)

    @classmethod
    def from_env(cls) -> "EnrichConfig":
        """Build config from environment variables."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is required for the enrich module."
            )

        return cls(
            anthropic_api_key=api_key,
            model=os.environ.get("ENRICH_MODEL", "claude-sonnet-4-20250514"),
            clay_webhook_url=os.environ.get("CLAY_WEBHOOK_URL"),
            clay_api_key=os.environ.get("CLAY_API_KEY"),
            max_pages_per_site=int(os.environ.get("ENRICH_MAX_PAGES", "4")),
            timeout=int(os.environ.get("ENRICH_TIMEOUT", "15")),
        )
