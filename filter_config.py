"""
Filter configuration for the HubSpot sync.

Edit this file to customize which organizations and contacts are synced.
"""

import re

# =============================================================================
# BLACKLISTED ORGANIZATION IDS
# =============================================================================
# Organizations with these IDs will be completely skipped during sync.
# Add UUIDs as strings.

BLACKLISTED_ORG_IDS = {
    # Example:
    # "550e8400-e29b-41d4-a716-446655440000",
}


# =============================================================================
# BLACKLISTED EMAIL DOMAINS
# =============================================================================
# Contacts with emails from these domains will be skipped.
# Also used to filter out internal/test organizations.

BLACKLISTED_EMAIL_DOMAINS = {
    # Internal domains
    "relu.eu",
    
    # Add more domains as needed:
    # "test.com",
    # "example.com",
}


# =============================================================================
# BLACKLISTED EMAIL PATTERNS
# =============================================================================
# Contacts with emails matching these patterns (case-insensitive) will be skipped.
# Uses simple substring matching.

BLACKLISTED_EMAIL_PATTERNS = {
    # Example patterns:
    # "+test@",
    # "noreply@",
}


# =============================================================================
# DISPOSABLE EMAIL DOMAINS (SPAM INDICATORS)
# =============================================================================
# Emails from these domains are flagged as likely spam.
# The account is still synced but marked with likely_spam=true.

DISPOSABLE_EMAIL_DOMAINS = {
    # Known disposable/temporary email services
    "emailwww.pro",
    "webxios.pro",
    "webxio.pro",
    "emaily.pro",
    "tempmail.com",
    "throwaway.email",
    "guerrillamail.com",
    "10minutemail.com",
    "mailinator.com",
    "temp-mail.org",
    "fakeinbox.com",
    "trashmail.com",
    "sharklasers.com",
    "yopmail.com",
    "mozmail.com",  # Firefox Relay aliases
    
    # Add more as discovered:
}


# =============================================================================
# SPAM USERNAME PATTERNS
# =============================================================================
# Regex patterns for usernames that look like spam/bot accounts.
# Matches against the local part of the email (before @).
#
# IMPORTANT: Be conservative here! False positives (marking legitimate users as spam)
# are worse than false negatives. Only flag patterns that are very clearly bot-generated.

SPAM_USERNAME_PATTERNS = [
    # Hex strings ONLY (8+ chars of exactly hex digits 0-9, a-f)
    # e.g., "0b523094cf", "a1b2c3d4e5"
    r"^[0-9a-f]{8,}$",
    
    # Pure numeric usernames (10+ digits) - common in Chinese spam
    # e.g., "15757123671", "2370332675"
    r"^\d{10,}$",
]

# Compiled patterns for performance
_SPAM_USERNAME_REGEXES = [re.compile(p, re.IGNORECASE) for p in SPAM_USERNAME_PATTERNS]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def is_org_blacklisted(org_id: str) -> bool:
    """Check if an organization ID is blacklisted."""
    return org_id in BLACKLISTED_ORG_IDS


def is_email_blacklisted(email: str) -> bool:
    """
    Check if an email should be filtered out.
    
    Returns True if the email:
    - Is from a blacklisted domain
    - Matches a blacklisted pattern
    """
    if not email:
        return False
    
    email_lower = email.lower().strip()
    
    # Check domain
    if "@" in email_lower:
        domain = email_lower.split("@")[-1]
        if domain in BLACKLISTED_EMAIL_DOMAINS:
            return True
    
    # Check patterns
    for pattern in BLACKLISTED_EMAIL_PATTERNS:
        if pattern.lower() in email_lower:
            return True
    
    return False


def filter_emails(emails: list[str]) -> list[str]:
    """Filter out blacklisted emails from a list."""
    return [e for e in emails if not is_email_blacklisted(e)]


def is_org_internal(admin_email: str, user_emails: list[str] = None) -> bool:
    """
    Check if an organization appears to be internal/test.
    
    Returns True if:
    - Admin email is from a blacklisted domain
    - All user emails are from blacklisted domains
    """
    if admin_email and is_email_blacklisted(admin_email):
        return True
    
    if user_emails:
        non_blacklisted = filter_emails(user_emails)
        if len(user_emails) > 0 and len(non_blacklisted) == 0:
            # All emails are blacklisted = internal org
            return True
    
    return False


def is_disposable_email_domain(email: str) -> bool:
    """Check if email is from a known disposable email domain."""
    if not email or "@" not in email:
        return False
    domain = email.lower().split("@")[-1]
    return domain in DISPOSABLE_EMAIL_DOMAINS


def has_spam_username_pattern(email: str) -> bool:
    """Check if email username matches spam patterns (hex strings, pure numeric)."""
    if not email or "@" not in email:
        return False
    username = email.lower().split("@")[0]
    return any(regex.match(username) for regex in _SPAM_USERNAME_REGEXES)


def is_likely_spam(
    admin_email: str,
    has_real_usage: bool = False,
    has_paddle_subscription: bool = False,
) -> bool:
    """
    Determine if an organization is likely spam.
    
    Returns True if the account shows spam signals AND has no real activity.
    Real activity (usage or Paddle subscription) clears the spam flag.
    
    Args:
        admin_email: The organization's admin email
        has_real_usage: Whether the org has real platform usage (orders beyond GIFT_TOPUP)
        has_paddle_subscription: Whether the org has an active Paddle subscription
        
    Returns:
        True if likely spam, False otherwise
    """
    # Real activity clears spam flag
    if has_real_usage or has_paddle_subscription:
        return False
    
    # Check spam signals
    if is_disposable_email_domain(admin_email):
        return True
    
    if has_spam_username_pattern(admin_email):
        return True
    
    return False


def get_spam_reason(admin_email: str) -> str | None:
    """
    Get the reason why an email is flagged as likely spam.
    
    Returns:
        Human-readable reason, or None if not spam
    """
    if is_disposable_email_domain(admin_email):
        domain = admin_email.split("@")[-1] if "@" in admin_email else "unknown"
        return f"disposable email domain ({domain})"
    
    if has_spam_username_pattern(admin_email):
        return "suspicious username pattern"
    
    return None
