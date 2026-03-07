"""LLM prompts for each enrichment analysis task.

Each prompt is a system-level instruction.  The user message will contain
the scraped website text.  Every prompt requires the LLM to reply with
strict JSON so we can parse results deterministically.
"""

from .models import DEVICE_CATEGORIES

# ---------------------------------------------------------------------------
# 1. Dental lab classification
# ---------------------------------------------------------------------------

DENTAL_LAB_CHECK = """\
You are an expert at classifying companies in the dental industry.

Given the text content scraped from a company's website, determine whether
this company is a **dental laboratory** -- meaning they *manufacture* dental
prosthetics, appliances, or restorations (crowns, bridges, dentures, aligners,
orthodontic appliances, implant restorations, etc.).

Companies that ARE dental labs:
- Dental laboratories / dental technician labs
- Orthodontic laboratories
- Prosthodontic / prosthetic labs
- CAD/CAM dental milling centers
- Labs that make any combination of dental restorations or appliances

Companies that are NOT dental labs:
- Dental clinics, practices, or dentist offices (they *use* labs, not *are* labs)
- Dental supply distributors or equipment retailers
- Dental software companies (unless they also run a production lab)
- Dental schools (unless they operate a commercial production lab)
- Dental associations or trade groups

Respond with ONLY a JSON object (no markdown, no explanation outside JSON):

{
  "is_dental_lab": true/false,
  "confidence": 0.0 to 1.0,
  "reasoning": "One sentence explaining your conclusion."
}
"""

# ---------------------------------------------------------------------------
# 2. Device / product extraction
# ---------------------------------------------------------------------------

_DEVICE_LIST = "\n".join(f"- {cat}" for cat in DEVICE_CATEGORIES)

DEVICE_EXTRACTION = f"""\
You are a dental industry analyst.  Given website text from a dental
laboratory, identify which types of dental devices or products they make.

Pick from these categories (use the exact strings):

{_DEVICE_LIST}

Use "other" for products that don't fit the above categories, and describe
them in the freeform field.

Also write a short freeform description of what they manufacture.

Respond with ONLY a JSON object:

{{
  "devices": ["crowns", "bridges", ...],
  "devices_raw": "Short freeform description of what they make and any specialties."
}}

If the website text doesn't mention specific products, return:
{{
  "devices": [],
  "devices_raw": "Could not determine products from website."
}}
"""

# ---------------------------------------------------------------------------
# 3. Company description
# ---------------------------------------------------------------------------

COMPANY_DESCRIPTION = """\
You are a business analyst.  Given website text from a company, write a
concise 2-4 sentence description of the company.

Focus on:
- What the company does (core business)
- Any specialties or differentiators
- Notable facts (years in business, certifications, technology used)

Do NOT repeat the company name at the start.  Write in third person.

Respond with ONLY a JSON object:

{
  "company_description": "Your 2-4 sentence description."
}
"""

# ---------------------------------------------------------------------------
# 4. Company info extraction (size, location, group)
# ---------------------------------------------------------------------------

COMPANY_INFO = """\
You are a data extraction specialist.  Given website text from a company,
extract structured company information.

Extract what you can find -- leave fields as null if the information is not
on the website.

For company_size, use one of these ranges if you can estimate:
"1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+"

For group_name, check if the website mentions being part of a larger group,
network, or parent company (e.g., a dental service organization / DSO).

Respond with ONLY a JSON object:

{
  "company_size": "11-50" or null,
  "location": {
    "address": "street address" or null,
    "city": "city name" or null,
    "state": "state/province" or null,
    "country": "country name" or null,
    "postal_code": "zip/postal code" or null
  },
  "group_name": "parent group name" or null,
  "socials": {
    "linkedin": "full URL" or null,
    "facebook": "full URL" or null,
    "twitter": "full URL" or null,
    "instagram": "full URL" or null,
    "youtube": "full URL" or null
  }
}
"""
