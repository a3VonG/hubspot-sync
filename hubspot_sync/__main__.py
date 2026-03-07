"""
Entry point for running hubspot_sync as a module.

Usage:
    python -m hubspot_sync                  # Run full sync (legacy combined mode)
    python -m hubspot_sync --dry-run        # Preview changes
    python -m hubspot_sync --org-id UUID    # Sync specific organization
"""

from .sync import main

if __name__ == "__main__":
    main()
