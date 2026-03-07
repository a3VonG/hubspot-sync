"""Enrichment orchestrator.

Scrapes the company website once, then runs each LLM analysis prompt
against the scraped content.  Merges all partial results into a single
``EnrichmentResult``.
"""

import logging
from typing import Optional

from . import prompts
from .clay import clay_enrich
from .config import EnrichConfig
from .llm import LLMError, extract_json
from .models import (
    CompanyLocation,
    CompanySocials,
    ContactInfo,
    EnrichmentResult,
)
from .scraper import ScrapedSite, scrape_site

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_company(
    name: str,
    domain: str,
    config: Optional[EnrichConfig] = None,
) -> EnrichmentResult:
    """Enrich a single company by name and domain.

    Parameters
    ----------
    name:
        Company name (used in the result; not required for scraping).
    domain:
        Company domain (e.g. ``"acmelabs.com"``).
    config:
        Optional configuration.  Falls back to ``EnrichConfig.from_env()``.

    Returns
    -------
    EnrichmentResult
        Dataclass with all enrichment fields populated where possible.
        Check ``result.errors`` for any non-fatal issues.
    """
    if config is None:
        config = EnrichConfig.from_env()

    result = EnrichmentResult(company_name=name, domain=domain)

    # ---- Step 1: Scrape website ------------------------------------------
    site = scrape_site(
        domain,
        max_pages=config.max_pages_per_site,
        timeout=config.timeout,
    )
    result.errors.extend(site.errors)

    if not site.pages:
        result.errors.append(f"Could not scrape any pages from {domain}")
        # Still try Clay if configured
        _run_clay(result, config)
        return result

    website_text = site.all_text

    # ---- Step 2: LLM enrichments (one per prompt) -----------------------
    _run_dental_lab_check(result, website_text, config)
    _run_device_extraction(result, website_text, config)
    _run_company_description(result, website_text, config)
    _run_company_info(result, website_text, config)

    # ---- Step 3: Clay enrichments (if configured) -----------------------
    _run_clay(result, config)

    return result


# ---------------------------------------------------------------------------
# Individual LLM enrichers
# ---------------------------------------------------------------------------

def _run_dental_lab_check(
    result: EnrichmentResult,
    website_text: str,
    config: EnrichConfig,
) -> None:
    """Determine if the company is a dental lab."""
    try:
        data = extract_json(
            system_prompt=prompts.DENTAL_LAB_CHECK,
            user_content=website_text,
            api_key=config.anthropic_api_key,
            model=config.model,
        )
        result.is_dental_lab = bool(data.get("is_dental_lab"))
        result.dental_lab_confidence = _clamp(
            float(data.get("confidence", 0)), 0.0, 1.0
        )
        result.dental_lab_reasoning = data.get("reasoning")
    except (LLMError, ValueError, TypeError) as exc:
        result.errors.append(f"dental_lab_check failed: {exc}")
        log.warning("dental_lab_check failed for %s: %s", result.domain, exc)


def _run_device_extraction(
    result: EnrichmentResult,
    website_text: str,
    config: EnrichConfig,
) -> None:
    """Extract device categories and freeform product description."""
    try:
        data = extract_json(
            system_prompt=prompts.DEVICE_EXTRACTION,
            user_content=website_text,
            api_key=config.anthropic_api_key,
            model=config.model,
        )
        result.devices = data.get("devices", [])
        result.devices_raw = data.get("devices_raw")
    except (LLMError, ValueError, TypeError) as exc:
        result.errors.append(f"device_extraction failed: {exc}")
        log.warning("device_extraction failed for %s: %s", result.domain, exc)


def _run_company_description(
    result: EnrichmentResult,
    website_text: str,
    config: EnrichConfig,
) -> None:
    """Generate a concise company description."""
    try:
        data = extract_json(
            system_prompt=prompts.COMPANY_DESCRIPTION,
            user_content=website_text,
            api_key=config.anthropic_api_key,
            model=config.model,
        )
        result.company_description = data.get("company_description")
    except (LLMError, ValueError, TypeError) as exc:
        result.errors.append(f"company_description failed: {exc}")
        log.warning("company_description failed for %s: %s", result.domain, exc)


def _run_company_info(
    result: EnrichmentResult,
    website_text: str,
    config: EnrichConfig,
) -> None:
    """Extract company size, location, group, and socials from website."""
    try:
        data = extract_json(
            system_prompt=prompts.COMPANY_INFO,
            user_content=website_text,
            api_key=config.anthropic_api_key,
            model=config.model,
        )

        result.company_size = data.get("company_size")
        result.group_name = data.get("group_name")

        # Location
        loc = data.get("location")
        if isinstance(loc, dict) and any(loc.values()):
            result.location = CompanyLocation(
                address=loc.get("address"),
                city=loc.get("city"),
                state=loc.get("state"),
                country=loc.get("country"),
                postal_code=loc.get("postal_code"),
            )

        # Socials
        soc = data.get("socials")
        if isinstance(soc, dict) and any(soc.values()):
            result.socials = CompanySocials(
                linkedin=soc.get("linkedin"),
                facebook=soc.get("facebook"),
                twitter=soc.get("twitter"),
                instagram=soc.get("instagram"),
                youtube=soc.get("youtube"),
            )

    except (LLMError, ValueError, TypeError) as exc:
        result.errors.append(f"company_info failed: {exc}")
        log.warning("company_info failed for %s: %s", result.domain, exc)


# ---------------------------------------------------------------------------
# Clay enrichment
# ---------------------------------------------------------------------------

def _run_clay(result: EnrichmentResult, config: EnrichConfig) -> None:
    """Run Clay enrichments if configured.  Merges into *result*."""
    if not config.clay_enabled:
        return

    try:
        clay_data = clay_enrich(
            company_name=result.company_name,
            domain=result.domain,
            config=config,
        )
        if clay_data is None:
            return

        # Clay can supplement fields that the LLM didn't find.
        # We don't overwrite LLM results -- Clay fills gaps.
        if result.company_size is None and clay_data.get("company_size"):
            result.company_size = clay_data["company_size"]

        if result.socials is None and clay_data.get("socials"):
            soc = clay_data["socials"]
            result.socials = CompanySocials(**soc)

        if result.location is None and clay_data.get("location"):
            loc = clay_data["location"]
            result.location = CompanyLocation(**loc)

        if not result.decision_makers and clay_data.get("decision_makers"):
            result.decision_makers = [
                ContactInfo(**c) for c in clay_data["decision_makers"]
            ]

    except Exception as exc:
        result.errors.append(f"clay_enrich failed: {exc}")
        log.warning("clay_enrich failed for %s: %s", result.domain, exc)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
