"""Website browsing tool - fetch and extract text content from URLs.

No external dependencies beyond requests. Uses regex-based HTML stripping
which is good enough for determining if a site is a dental lab.
"""

import re
import requests

SCHEMA = {
    "name": "browse_website",
    "description": (
        "Fetch a website and return its text content. Use this to examine a "
        "company's website, check if it's a dental lab, read directories, etc. "
        "Returns up to ~8000 characters of text content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to fetch (e.g. https://example.com)",
            }
        },
        "required": ["url"],
    },
}

MAX_CONTENT_LENGTH = 8000


def execute(url: str) -> str:
    """Fetch a URL and return extracted text content."""
    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LeadResearchBot/1.0)"},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        return f"Error fetching {url}: {e}"

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return f"Non-HTML content type: {content_type}"

    text = _html_to_text(resp.text)

    if len(text) < 50:
        return f"Page at {url} appears empty or is dynamically loaded (JavaScript-only)."

    if len(text) > MAX_CONTENT_LENGTH:
        text = text[:MAX_CONTENT_LENGTH] + "\n\n[... truncated ...]"

    return f"Content from {url}:\n\n{text}"


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
        ("&rsquo;", "'"),
        ("&lsquo;", "'"),
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
