"""
Domain extraction and validation utilities.
"""

from config import Config


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


def is_generic_domain(domain: str, config: Config = None) -> bool:
    """
    Check if a domain is a generic/free email provider.
    
    Args:
        domain: Email domain to check
        config: Optional config with custom generic domains list
        
    Returns:
        True if domain is a generic email provider
    """
    if not domain:
        return True
    
    domain = domain.lower().strip()
    
    # Use config's generic domains if provided, otherwise use default list
    generic_domains = (
        config.generic_email_domains 
        if config 
        else (
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
    )
    
    return domain in generic_domains


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
