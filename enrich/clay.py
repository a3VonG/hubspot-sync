"""Clay API client stub.

Clay doesn't expose a traditional REST API -- it works as a platform where
you set up tables with enrichment columns, then push/pull data via webhooks.

This module defines the interface.  The actual HTTP/webhook integration
will be wired up once a Clay table is configured.  Until then,
``clay_enrich`` returns ``None`` and the orchestrator skips it.

When implementing, the expected flow is:
1. POST company data (name + domain) to the Clay webhook URL.
2. Clay runs its configured enrichments asynchronously.
3. Poll or receive a callback with the enriched row.
4. Return the enriched data as a dict matching the schema below.

Expected return schema::

    {
        "company_size": "11-50",
        "location": {
            "address": "...", "city": "...", "state": "...",
            "country": "...", "postal_code": "..."
        },
        "socials": {
            "linkedin": "...", "facebook": "...", "twitter": "...",
            "instagram": "...", "youtube": "..."
        },
        "decision_makers": [
            {"name": "...", "title": "...", "email": "...",
             "linkedin": "...", "phone": "..."},
        ]
    }
"""

import logging
from typing import Any, Optional

from .config import EnrichConfig

log = logging.getLogger(__name__)


def clay_enrich(
    *,
    company_name: str,
    domain: str,
    config: EnrichConfig,
) -> Optional[dict[str, Any]]:
    """Enrich a company via Clay.

    Returns a dict with supplemental data, or ``None`` if Clay is not
    configured or the enrichment fails.
    """
    if not config.clay_enabled:
        return None

    # TODO: implement Clay webhook integration
    #
    # Rough sketch:
    #
    #   resp = requests.post(
    #       config.clay_webhook_url,
    #       headers={"Authorization": f"Bearer {config.clay_api_key}"},
    #       json={"company_name": company_name, "domain": domain},
    #       timeout=config.timeout,
    #   )
    #   resp.raise_for_status()
    #   return resp.json()
    #
    log.info(
        "Clay enrichment not yet implemented (company=%s, domain=%s)",
        company_name,
        domain,
    )
    return None
