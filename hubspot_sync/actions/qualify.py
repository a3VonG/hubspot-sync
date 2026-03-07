"""
Account qualification logic.

Determines the account_qualification_status for a HubSpot company based on
the admin email and subscription status.

Statuses:
    - "Qualified": Has an active Paddle subscription, or enrich determines so
    - "Pending Review": Uses a personal/public email domain (gmail, etc.)
    - "Rejected": Uses a disposable/throwaway email domain

The qualify_account function is called whenever account_qualification_status
is empty — both during company creation (sync_organizations) and during
analytics refresh (sync_analytics).
"""

import os
import logging
from typing import Optional

import requests

from ..filter_config import is_disposable_email_domain
from ..utils.domains import extract_domain, is_generic_domain

logger = logging.getLogger(__name__)

PROP_QUALIFICATION_STATUS = "account_qualification_status"

QUALIFICATION_QUALIFIED = "Qualified"
QUALIFICATION_PENDING = "Pending Review"
QUALIFICATION_REJECTED = "Rejected"


def _check_usercheck(email: str) -> Optional[dict]:
    """
    Query the Usercheck API for email intelligence.

    Returns the parsed JSON response, or None on failure / missing API key.
    """
    api_key = os.environ.get("USERCHECK_API_KEY")
    if not api_key:
        return None

    try:
        resp = requests.get(
            f"https://api.usercheck.com/email/{email}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Usercheck returned status %s for %s", resp.status_code, email)
    except Exception as exc:
        logger.warning("Usercheck request failed for %s: %s", email, exc)

    return None


def qualify_account(
    email: str,
    has_active_subscription: bool = False,
    config=None,
) -> str:
    """
    Determine the account_qualification_status for a given email.

    Decision tree:
    1. Active Paddle subscription → "Qualified"
    2. Disposable / throwaway email → "Rejected"
    3. Usercheck says disposable → "Rejected"
    4. Generic / public-domain email (gmail, etc.) or Usercheck says
       public_domain → "Pending Review"
    5. Business email → print placeholder for enrich and return "Pending Review"
       (enrich will upgrade to "Qualified" if warranted)

    Args:
        email: The admin email address to evaluate.
        has_active_subscription: Whether the org has an active Paddle subscription.
        config: Optional Config for accessing generic domain list.

    Returns:
        One of "Qualified", "Pending Review", or "Rejected".
    """
    if has_active_subscription:
        return QUALIFICATION_QUALIFIED

    if not email:
        return QUALIFICATION_PENDING

    if is_disposable_email_domain(email):
        return QUALIFICATION_REJECTED

    domain = extract_domain(email)

    uc_data = _check_usercheck(email)

    if uc_data:
        if uc_data.get("disposable"):
            return QUALIFICATION_REJECTED
        if uc_data.get("public_domain"):
            return QUALIFICATION_PENDING

    if domain and is_generic_domain(domain, config):
        return QUALIFICATION_PENDING

    # Business / custom domain — leave a placeholder for enrich to decide
    print(f"  *** ENRICH PLACEHOLDER: Determine qualification for {email} (domain: {domain}) ***")
    return QUALIFICATION_PENDING
