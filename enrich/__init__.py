"""Company enrichment module.

Enriches a company by name + domain through website scraping, LLM analysis,
and (optionally) Clay.  Returns a structured ``EnrichmentResult`` dataclass.

Quick start::

    from enrich import enrich_company, EnrichConfig

    config = EnrichConfig.from_env()
    result = enrich_company("Acme Dental Lab", "acmedentallab.com", config)

    print(result.is_dental_lab)        # True / False / None
    print(result.devices)              # ["crowns", "bridges", ...]
    print(result.company_description)  # "..."
"""

from .config import EnrichConfig
from .enricher import enrich_company
from .models import (
    DEVICE_CATEGORIES,
    CompanyLocation,
    CompanySocials,
    ContactInfo,
    EnrichmentResult,
)

__all__ = [
    "enrich_company",
    "EnrichConfig",
    "EnrichmentResult",
    "CompanySocials",
    "CompanyLocation",
    "ContactInfo",
    "DEVICE_CATEGORIES",
]
