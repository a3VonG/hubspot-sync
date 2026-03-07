"""Website scraper for enrichment.

Fetches the homepage and a handful of common sub-pages (about, services,
products, contact) to build a text corpus for LLM analysis.

No external dependencies beyond *requests*.  HTML → text conversion uses
regex-based stripping (no BS4 / lxml needed).
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests

log = logging.getLogger(__name__)

# Sub-paths to attempt after the homepage.  Order matters: earlier paths
# are prioritised when we hit *max_pages*.
_SUBPATHS = [
    "/about",
    "/about-us",
    "/services",
    "/products",
    "/our-services",
    "/our-products",
    "/contact",
    "/team",
]

_USER_AGENT = "Mozilla/5.0 (compatible; CompanyEnrichBot/1.0)"


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ScrapedSite:
    """Text content scraped from a company website."""

    domain: str
    pages: dict[str, str] = field(default_factory=dict)
    """Mapping of path → extracted text content (e.g. "/" → "…")."""

    errors: list[str] = field(default_factory=list)
    """Non-fatal fetch errors (e.g. 404 on /services)."""

    @property
    def all_text(self) -> str:
        """Concatenated text from every successfully scraped page."""
        parts: list[str] = []
        for path, text in self.pages.items():
            parts.append(f"=== Page: {path} ===\n{text}")
        return "\n\n".join(parts)

    @property
    def homepage(self) -> Optional[str]:
        return self.pages.get("/")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_site(
    domain: str,
    *,
    max_pages: int = 4,
    timeout: int = 15,
) -> ScrapedSite:
    """Scrape a company website and return extracted text.

    Fetches the homepage first.  Then tries common sub-paths until we hit
    *max_pages* successful fetches or exhaust the list.
    """
    base_url = _normalise_base(domain)
    result = ScrapedSite(domain=domain)

    # --- Homepage (always) ------------------------------------------------
    text = _fetch_page(base_url, timeout=timeout)
    if text is not None:
        result.pages["/"] = text
    else:
        result.errors.append(f"Failed to fetch homepage: {base_url}")
        return result  # no point trying sub-pages

    # --- Sub-pages --------------------------------------------------------
    for subpath in _SUBPATHS:
        if len(result.pages) >= max_pages:
            break
        url = urljoin(base_url, subpath)
        text = _fetch_page(url, timeout=timeout)
        if text is not None:
            # Avoid storing duplicates (some /about redirects to /)
            if not _is_duplicate(text, result.pages):
                result.pages[subpath] = text
        # 404 / errors on sub-paths are expected; don't log as errors

    log.info(
        "Scraped %s: %d pages (%s)",
        domain,
        len(result.pages),
        ", ".join(result.pages.keys()),
    )
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_base(domain: str) -> str:
    """Ensure the domain has a scheme and trailing slash."""
    domain = domain.strip().rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    return domain + "/"


def _fetch_page(url: str, *, timeout: int = 15) -> Optional[str]:
    """Fetch a single URL and return extracted text, or None on failure."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return None

    text = _html_to_text(resp.text)
    if len(text) < 50:
        return None  # likely JS-only or empty page

    # Cap individual page length to keep total prompt size manageable.
    if len(text) > 12_000:
        text = text[:12_000] + "\n\n[... truncated ...]"

    return text


def _is_duplicate(
    new_text: str, existing: dict[str, str], *, threshold: float = 0.85
) -> bool:
    """Quick check: if >threshold of the new text's words appear in any
    existing page we treat it as a duplicate / redirect."""
    new_words = set(new_text.lower().split())
    if not new_words:
        return True
    for existing_text in existing.values():
        existing_words = set(existing_text.lower().split())
        overlap = len(new_words & existing_words) / len(new_words)
        if overlap >= threshold:
            return True
    return False


def _html_to_text(html: str) -> str:
    """Extract readable text from HTML using regex (no BS4 dependency)."""
    # Remove script, style, nav, footer blocks
    for tag in ("script", "style", "nav", "footer", "header"):
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE
        )

    # Convert common block elements to newlines
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(p|div|h[1-6]|li|tr|td|th)>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(
        r"<(p|div|h[1-6]|li|tr|td|th)[^>]*>", "\n", html, flags=re.IGNORECASE
    )

    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", " ", html)

    # Decode common entities
    for entity, char in [
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&nbsp;", " "),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&rsquo;", "\u2019"),
        ("&lsquo;", "\u2018"),
        ("&rdquo;", "\u201d"),
        ("&ldquo;", "\u201c"),
        ("&mdash;", "\u2014"),
        ("&ndash;", "\u2013"),
    ]:
        text = text.replace(entity, char)

    # Clean up whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()
