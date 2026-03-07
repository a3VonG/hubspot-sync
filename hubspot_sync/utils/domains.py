"""
Domain extraction and validation utilities.

Uses a comprehensive list of free/generic email provider domains fetched
from a public gist and cached locally (see generic_domains.py), combined
with base-name matching for providers with many regional TLD variants
(e.g. outlook.it, hotmail.co.uk, yahoo.fr), and the disposable-email-domains
package for throwaway/temporary email services.
"""

from ..config import Config
from ..filter_config import DISPOSABLE_EMAIL_DOMAINS
from .generic_domains import load_generic_domains

try:
    from disposable_email_domains import blocklist as _disposable_blocklist
except ImportError:
    _disposable_blocklist = set()


def extract_domain(email: str) -> str | None:
    """
    Extract domain from an email address.
    
    Args:
        email: Email address
        
    Returns:
        Domain part of the email, or None if invalid
    """
    if not email or "@" not in email:
        return None
    
    try:
        domain = email.split("@")[1].lower().strip()
        return domain if domain else None
    except (IndexError, AttributeError):
        return None


# Base-name prefixes: matches any TLD variant.
# e.g. "outlook" matches outlook.com, outlook.it, outlook.co.uk, etc.
# Only include names where regional variants are common and the base name
# is unambiguously a free email provider.
_GENERIC_BASE_NAMES = {
    "outlook",
    "hotmail",
    "live",
    "yahoo",
    "ymail",
    "aol",
    "zoho",
    "yandex",
    "gmx",
    "mail",
    "fastmail",
    "tutanota",
}


def is_generic_domain(domain: str, config: Config = None) -> bool:
    """
    Check if a domain is a generic/free email provider or a disposable domain.
    
    Uses five layers:
    1. Config-provided custom domains (if any).
    2. The comprehensive fetched + fallback domain list (~6000 domains).
    3. The disposable-email-domains package blocklist (~3000 throwaway domains)
       + our curated DISPOSABLE_EMAIL_DOMAINS from filter_config.py.
    4. Base-name matching for providers with many regional TLDs.
    
    Args:
        domain: Email domain to check
        config: Optional config with custom generic domains list
        
    Returns:
        True if domain is a generic email provider or disposable
    """
    if not domain:
        return True
    
    domain = domain.lower().strip()
    
    # 1. Config-provided custom domains
    if config and config.generic_email_domains:
        if domain in config.generic_email_domains:
            return True
    
    # 2. Comprehensive list (fetched gist + hardcoded fallback)
    all_domains = load_generic_domains()
    if domain in all_domains:
        return True
    
    # 3. Disposable/throwaway email domains (package + our curated list)
    if domain in _disposable_blocklist or domain in DISPOSABLE_EMAIL_DOMAINS:
        return True
    
    # 4. Base-name matching for regional variants not in the list
    base_name = domain.split(".")[0]
    return base_name in _GENERIC_BASE_NAMES


def get_organization_domains(emails: list[str], config: Config = None) -> set[str]:
    """
    Extract unique non-generic domains from a list of emails.
    
    Args:
        emails: List of email addresses
        config: Optional config with custom generic domains list
        
    Returns:
        Set of unique, non-generic domains
    """
    domains = set()
    for email in emails:
        domain = extract_domain(email)
        if domain and not is_generic_domain(domain, config):
            domains.add(domain)
    return domains
