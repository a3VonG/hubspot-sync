"""
Generic / free email domain detection.

Maintains a cached list of free email provider domains fetched from a public
gist (originally sourced from HubSpot). The list is refreshed every 7 days
by default. Between refreshes (or when offline) the cached file is used.
A hardcoded fallback covers the most common providers so the system works
even if the file has never been fetched.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Where we cache the downloaded list (next to this file)
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "free_email_domains.txt"

# Raw gist URL (plain text, one domain per line)
_GIST_URL = (
    "https://gist.githubusercontent.com/ammarshah/"
    "f5c2624d767f91a7cbdc4e54db8dd0bf/raw/all_email_provider_domains.txt"
)

# Re-fetch if older than this many seconds (default 7 days)
_MAX_AGE_SECONDS = int(os.environ.get("GENERIC_DOMAINS_MAX_AGE", 7 * 24 * 3600))

# Module-level cache so we only load once per process
_loaded_domains: Optional[set[str]] = None


# ---------------------------------------------------------------------------
# Hardcoded fallback – covers the most common providers so the system works
# even without a network fetch.
# ---------------------------------------------------------------------------
FALLBACK_DOMAINS = {
    # Google
    "gmail.com", "googlemail.com",
    # Microsoft
    "outlook.com", "hotmail.com", "live.com", "msn.com", "windowslive.com",
    # Apple
    "icloud.com", "me.com", "mac.com",
    # Yahoo
    "yahoo.com", "ymail.com", "rocketmail.com",
    # Privacy / encrypted
    "protonmail.com", "proton.me", "protonmail.ch",
    "tutanota.com", "tutanota.de", "tutamail.com", "tuta.io", "keemail.me",
    "hushmail.com",
    # Other major global
    "aol.com", "mail.com", "zoho.com", "yandex.com", "yandex.ru",
    "gmx.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch",
    "fastmail.com", "fastmail.fm",
    # German
    "web.de", "t-online.de", "freenet.de", "arcor.de", "posteo.de",
    # French
    "laposte.net", "orange.fr", "free.fr", "sfr.fr", "wanadoo.fr",
    # Italian
    "libero.it", "virgilio.it", "alice.it", "tin.it", "tiscali.it",
    # Russian
    "mail.ru", "bk.ru", "inbox.ru", "list.ru", "rambler.ru",
    # Chinese
    "qq.com", "163.com", "126.com", "sina.com", "sohu.com",
    "aliyun.com", "foxmail.com", "yeah.net",
    # Korean
    "naver.com", "daum.net", "hanmail.net",
    # Brazilian
    "bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br", "globo.com",
    # Polish
    "wp.pl", "onet.pl", "interia.pl", "o2.pl",
    # Czech
    "seznam.cz",
    # Indian
    "rediffmail.com", "rediff.com",
    # US ISPs
    "att.net", "comcast.net", "cox.net", "charter.net", "verizon.net",
    "earthlink.net", "bellsouth.net", "sbcglobal.net", "centurylink.net",
    "windstream.net", "frontier.com", "spectrum.net", "optimum.net",
    "roadrunner.com", "juno.com", "netzero.com",
    # UK ISPs
    "btinternet.com", "sky.com", "virginmedia.com", "talktalk.co.uk",
    "ntlworld.com", "blueyonder.co.uk",
    # Canadian ISPs
    "shaw.ca", "rogers.com", "bell.net", "sympatico.ca", "videotron.ca",
    # Australian ISPs
    "bigpond.com", "bigpond.com.au", "optusnet.com.au", "telstra.com.au",
    "iinet.net.au", "tpg.com.au",
    # Dutch ISPs
    "ziggo.nl", "kpnmail.nl", "xs4all.nl", "upcmail.nl",
}


def _fetch_and_cache() -> Optional[set[str]]:
    """Download the domain list from the gist and cache it locally."""
    try:
        import urllib.request
        logger.info("Fetching generic email domains from gist...")
        req = urllib.request.Request(_GIST_URL, headers={"User-Agent": "hubspot-sync"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")

        domains = set()
        for line in text.splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                domains.add(line)

        if len(domains) < 100:
            logger.warning("Fetched domain list seems too small (%d), ignoring", len(domains))
            return None

        # Write cache
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text("\n".join(sorted(domains)), encoding="utf-8")
        logger.info("Cached %d generic email domains to %s", len(domains), _CACHE_FILE)
        return domains

    except Exception as e:
        logger.warning("Could not fetch generic email domains: %s", e)
        return None


def _load_from_cache() -> Optional[set[str]]:
    """Load domains from the local cache file."""
    if not _CACHE_FILE.exists():
        return None
    try:
        text = _CACHE_FILE.read_text(encoding="utf-8")
        domains = {line.strip().lower() for line in text.splitlines() if line.strip()}
        return domains if domains else None
    except Exception as e:
        logger.warning("Could not read cached domains: %s", e)
        return None


def _cache_is_stale() -> bool:
    """Check if the cache file is missing or older than _MAX_AGE_SECONDS."""
    if not _CACHE_FILE.exists():
        return True
    age = time.time() - _CACHE_FILE.stat().st_mtime
    return age > _MAX_AGE_SECONDS


def load_generic_domains(force_refresh: bool = False) -> set[str]:
    """
    Return the full set of generic/free email domains.

    Uses a three-tier strategy:
    1. If cache exists and is fresh, use it (+ fallback).
    2. If cache is stale or missing, try to fetch from the gist.
    3. If fetch fails, use stale cache or fallback.

    Results are cached in-process so subsequent calls are free.
    """
    global _loaded_domains

    if _loaded_domains is not None and not force_refresh:
        return _loaded_domains

    domains: Optional[set[str]] = None

    if force_refresh or _cache_is_stale():
        domains = _fetch_and_cache()

    if domains is None:
        domains = _load_from_cache()

    if domains is None:
        logger.info("Using hardcoded fallback generic domains list (%d domains)", len(FALLBACK_DOMAINS))
        domains = set()

    # Always include the fallback set so we never miss the big providers
    domains = domains | FALLBACK_DOMAINS

    _loaded_domains = domains
    return _loaded_domains
