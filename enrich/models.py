"""Data models for company enrichment results.

All result fields are Optional -- each enricher populates its own slice,
and callers inspect what they need.
"""

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Controlled vocabulary for dental device categories
# ---------------------------------------------------------------------------

DEVICE_CATEGORIES = [
    "dentures",
    "crowns",
    "bridges",
    "implants",
    "aligners",
    "orthodontic_expanders",
    "orthodontic_models",
    "night_guards",
    "veneers",
    "inlays_onlays",
    "surgical_guides",
    "removable_partials",
    "other",
]
"""Canonical device categories the LLM may assign.  Kept as a plain list
so prompts can embed them directly."""


# ---------------------------------------------------------------------------
# Sub-dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CompanySocials:
    """Social media links for a company."""
    linkedin: Optional[str] = None
    facebook: Optional[str] = None
    twitter: Optional[str] = None
    instagram: Optional[str] = None
    youtube: Optional[str] = None


@dataclass
class CompanyLocation:
    """Physical address / location."""
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None


@dataclass
class ContactInfo:
    """A decision-maker or key contact."""
    name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    linkedin: Optional[str] = None
    phone: Optional[str] = None


# ---------------------------------------------------------------------------
# Main enrichment result
# ---------------------------------------------------------------------------

@dataclass
class EnrichmentResult:
    """Aggregated enrichment data for a single company.

    Populated incrementally by one or more enrichers.  Every field except
    *company_name* and *domain* is optional so partial results are valid.
    """

    company_name: str
    domain: str

    # --- Website analysis (LLM) -------------------------------------------
    is_dental_lab: Optional[bool] = None
    dental_lab_confidence: Optional[float] = None  # 0.0 – 1.0
    dental_lab_reasoning: Optional[str] = None

    devices: Optional[list[str]] = None  # entries from DEVICE_CATEGORIES
    devices_raw: Optional[str] = None    # freeform description of what they make

    company_description: Optional[str] = None

    # --- Structured data (Clay or LLM fallback) ---------------------------
    company_size: Optional[str] = None       # e.g. "11-50", "51-200"
    socials: Optional[CompanySocials] = None
    location: Optional[CompanyLocation] = None
    group_name: Optional[str] = None         # parent group / DSO if any
    decision_makers: list[ContactInfo] = field(default_factory=list)

    # --- Metadata ---------------------------------------------------------
    errors: list[str] = field(default_factory=list)
    """Non-fatal errors encountered during enrichment."""
