#!/usr/bin/env python3
"""
Analytics Sync Entry Point.

Updates analytics properties for EXISTING linked companies:
1. Queries HubSpot for all companies with platform_organization_id
2. Fetches analytics data from platform DB + Paddle API
3. Updates HubSpot company properties

This is the "refresh" workflow that runs frequently to keep analytics current.
It does NOT create new companies or link organizations.

Usage:
    python sync_analytics.py                  # Full analytics refresh
    python sync_analytics.py --dry-run        # Preview changes
    python sync_analytics.py --org-id UUID    # Update specific org
    python sync_analytics.py --limit 100      # Limit companies processed

See ANALYTICS.md for property definitions and logic.
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional

# Load .env file automatically
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import Config
from filter_config import is_org_blacklisted
from clients.hubspot import HubSpotClient, Company
from clients.platform import PlatformClient
from analytics.platform_analytics import PlatformAnalyticsComputer
from analytics.billing_status import BillingStatusComputer
from analytics.models import OrganizationAnalytics
from utils.audit import AuditLog, SyncEventType


class AnalyticsSyncOrchestrator:
    """
    Orchestrator for syncing analytics FROM platform TO HubSpot.
    
    Starting point: HubSpot companies with platform_organization_id
    Data sources: Platform DB + Paddle API
    Output: Updated HubSpot company properties
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.audit_log = AuditLog()
        
        # Initialize clients
        self.hubspot = HubSpotClient(
            config.hubspot_api_key,
            config.hubspot_platform_org_id_property,
        )
        self.platform = PlatformClient(config.db_config)
        
        # Initialize billing computer (for filling in empty company names via Paddle Billing API)
        self.billing_computer = None
        if config.paddle_api_key and config.paddle_vendor_id:
            self.billing_computer = BillingStatusComputer(config.paddle_vendor_id, config.paddle_api_key)
        
        # Initialize analytics computer
        self.analytics_computer = PlatformAnalyticsComputer(
            config.db_config,
            config,
            config.paddle_vendor_id,
            config.paddle_api_key,
        )
        
        # Results tracking
        self.results = {
            "companies_found": 0,
            "companies_updated": 0,
            "companies_skipped": 0,
            "properties_updated": 0,
            "errors": 0,
        }
        self.errors: list[str] = []
    
    def run(
        self,
        org_id: Optional[str] = None,
        limit: Optional[int] = None,
        verbose: bool = True,
    ) -> dict:
        """
        Run analytics sync.
        
        Args:
            org_id: Optional specific organization ID to sync
            limit: Optional limit on companies to process
            verbose: Whether to print detailed output
            
        Returns:
            Results dictionary
        """
        self.audit_log.start_sync_run()
        start_time = datetime.now(timezone.utc)
        
        print(f"{'='*60}")
        print(f"Analytics Sync Started")
        print(f"Run ID: {self.audit_log.sync_run_id}")
        print(f"Dry Run: {self.config.dry_run}")
        print(f"{'='*60}")
        
        try:
            # Step 1: Get companies from HubSpot
            if org_id:
                # Get specific company (include billing fields to check if empty)
                company = self.hubspot.get_company_by_platform_org_id(
                    org_id,
                    extra_properties=["country", "city", "state", "zip", "vat_number"],
                )
                companies = [company] if company else []
                if not company:
                    print(f"\nNo company found with platform_org_id: {org_id}")
            else:
                # Get all linked companies (include billing fields to check if empty)
                print(f"\nQuerying HubSpot for companies with platform_organization_id...")
                companies = self.hubspot.get_all_companies_with_platform_org_id(
                    extra_properties=["country", "city", "state", "zip", "vat_number"],
                )
            
            self.results["companies_found"] = len(companies)
            print(f"Found {len(companies)} companies with platform_organization_id")
            
            if limit and len(companies) > limit:
                companies = companies[:limit]
                print(f"Processing first {limit} companies")
            
            if not companies:
                print("\nNo companies to process.")
                return self.results
            
            # Step 2: Get paddle_ids for these orgs from platform DB
            org_ids = [c.platform_org_id for c in companies if c.platform_org_id]
            paddle_map = self._get_paddle_ids(org_ids)
            
            # Step 3: Process each company
            print(f"\n{'='*60}")
            print("Processing companies...")
            print(f"{'='*60}")
            
            for i, company in enumerate(companies, 1):
                org_id = company.platform_org_id
                if not org_id:
                    continue
                
                company_name = company.name or f"Company #{company.id}"
                
                if verbose:
                    print(f"\n[{i}/{len(companies)}] {company_name}")
                    print(f"  Org ID: {org_id}")
                
                # Check blacklist
                if is_org_blacklisted(org_id):
                    self.results["companies_skipped"] += 1
                    if verbose:
                        print(f"  Skipped: blacklisted")
                    continue
                
                # Compute and sync analytics
                try:
                    paddle_id = paddle_map.get(org_id)
                    success = self._sync_company_analytics(
                        company, org_id, paddle_id, verbose
                    )
                    if success:
                        self.results["companies_updated"] += 1
                    else:
                        self.results["companies_skipped"] += 1
                except Exception as e:
                    error_msg = f"Error updating {company_name}: {str(e)}"
                    self.errors.append(error_msg)
                    self.results["errors"] += 1
                    if verbose:
                        print(f"  ERROR: {e}")
            
            self.audit_log.save()
            
        except Exception as e:
            error_msg = f"Fatal error: {str(e)}"
            print(f"\n{error_msg}")
            self.errors.append(error_msg)
            self.results["errors"] += 1
        
        finally:
            self.platform.close()
        
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        self._print_report(duration)
        
        return self.results
    
    def _get_paddle_ids(self, org_ids: list[str]) -> dict[str, str]:
        """
        Get paddle_id mapping for organizations from platform DB.
        
        Returns:
            Dict mapping org_id -> paddle_id
        """
        if not org_ids:
            return {}
        
        print(f"\nFetching paddle_ids from platform DB...")
        
        try:
            conn = self.platform._get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT id, paddle_id
                FROM organizations
                WHERE id = ANY(%(org_ids)s::uuid[]) AND paddle_id IS NOT NULL
                """,
                {"org_ids": org_ids}
            )
            
            result = {str(row[0]): row[1] for row in cursor.fetchall()}
            cursor.close()
            
            print(f"  Found paddle_ids for {len(result)} organizations")
            return result
            
        except Exception as e:
            print(f"  Warning: Could not fetch paddle_ids: {e}")
            return {}
    
    def _sync_company_analytics(
        self,
        company: Company,
        org_id: str,
        paddle_id: Optional[str],
        verbose: bool,
    ) -> bool:
        """
        Compute and sync analytics for a single company.
        
        Returns:
            True if analytics were updated
        """
        # Compute analytics
        try:
            analytics = self.analytics_computer.compute_for_organization(org_id, paddle_id)
        except Exception as e:
            if verbose:
                print(f"  Failed to compute analytics: {e}")
            return False
        
        # Convert to properties
        properties = analytics.to_hubspot_properties()
        
        # Fill empty company fields from Paddle billing data (only fetch what's needed)
        if self.billing_computer and paddle_id:
            needs = self._check_empty_billing_fields(company)
            if needs["any"]:
                try:
                    paddle_info = self.billing_computer.get_customer_info(
                        paddle_id,
                        need_name=needs["name"],
                        need_business=needs["business"],
                        need_address=needs["address"],
                    )
                    if paddle_info:
                        self._fill_empty_fields_from_paddle(
                            company, paddle_info, properties, verbose
                        )
                except Exception as e:
                    if verbose:
                        print(f"  Warning: Could not fetch Paddle customer info: {e}")
            elif verbose:
                print(f"  Billing fields already populated, skipping Paddle lookup")
        
        self.results["properties_updated"] += len(properties)
        
        if verbose:
            self._print_analytics_summary(analytics, properties)
        
        # Update HubSpot
        if self.config.dry_run:
            if verbose:
                print(f"  [DRY RUN] Would update {len(properties)} properties")
            return True
        
        success, error_detail = self.hubspot.update_company(company.id, properties)
        
        if success:
            if verbose:
                print(f"  ✓ Updated {len(properties)} properties")
            return True
        else:
            if verbose:
                print(f"  ✗ HubSpot update failed: {error_detail}")
            return False
    
    def _check_empty_billing_fields(self, company: Company) -> dict:
        """
        Check which billing-related fields are empty on the HubSpot company.
        
        Returns dict with flags indicating which Paddle API calls are needed:
        - name: need /customers (name is empty)
        - business: need /businesses (name or vat_number is empty)
        - address: need /addresses (country, city, state, or zip is empty)
        - any: True if any field is empty (shortcut)
        """
        props = company.properties or {}
        
        def is_empty(key: str) -> bool:
            val = props.get(key)
            return not val or not str(val).strip()
        
        name_empty = is_empty("name") and not company.name
        vat_empty = is_empty("vat_number")
        address_empty = (
            is_empty("country") or is_empty("city")
            or is_empty("state") or is_empty("zip")
        )
        
        # Business endpoint provides name override + vat_number
        need_business = name_empty or vat_empty
        # Customer endpoint provides base name (needed if name empty)
        need_name = name_empty
        # Address endpoint provides country, city, state, zip
        need_address = address_empty
        
        return {
            "name": need_name,
            "business": need_business,
            "address": need_address,
            "any": need_name or need_business or need_address,
        }
    
    def _fill_empty_fields_from_paddle(
        self,
        company: Company,
        paddle_info: dict,
        properties: dict,
        verbose: bool,
    ):
        """
        Fill empty HubSpot company fields from Paddle billing data.
        
        Only sets a field if it is currently empty on HubSpot, so that
        HubSpot remains the single source of truth for manually-entered data.
        """
        # Mapping: (HubSpot property, Paddle info key, display label)
        field_mapping = [
            ("name", "name", "Name"),
            ("country", "country_code", "Country"),
            ("city", "city", "City"),
            ("state", "region", "State/Region"),
            ("zip", "postal_code", "Postal Code"),
            ("vat_number", "tax_identifier", "VAT Number"),
        ]
        
        filled = []
        for hs_prop, paddle_key, label in field_mapping:
            paddle_value = paddle_info.get(paddle_key)
            if not paddle_value:
                continue
            
            # Check if the HubSpot property is currently empty
            current_value = company.properties.get(hs_prop) if company.properties else None
            # For "name", also check the company.name attribute
            if hs_prop == "name":
                current_value = current_value or company.name
            
            if not current_value or not str(current_value).strip():
                properties[hs_prop] = paddle_value
                filled.append(f"{label}={paddle_value}")
        
        if filled and verbose:
            print(f"  Setting from Paddle (was empty): {', '.join(filled)}")
    
    def _print_analytics_summary(
        self,
        analytics: OrganizationAnalytics,
        properties: dict,
    ):
        """Print a summary of computed analytics."""
        print(f"  Analytics computed ({len(properties)} properties):")
        
        # Key metrics
        print(f"    - billing_status: {analytics.billing_status}")
        print(f"    - is_testing: {analytics.is_testing}")
        print(f"    - usage_last_30_days: {analytics.usage_last_30_days}")
        print(f"    - usage_trend: {analytics.usage_trend}")
        
        if analytics.services_used_last_30_days:
            services = analytics.services_used_last_30_days[:50]
            if len(analytics.services_used_last_30_days) > 50:
                services += "..."
            print(f"    - services_last_30d: {services}")
        
        if analytics.is_testing:
            print(f"    - testing_credits: {analytics.testing_free_credits_remaining}")
        
        if analytics.number_errors_last_30_days > 0:
            print(f"    - errors_last_30d: {analytics.number_errors_last_30_days}")
    
    def _print_report(self, duration: float):
        """Print summary report."""
        print(f"\n{'='*60}")
        print(f"ANALYTICS SYNC COMPLETE")
        print(f"{'='*60}")
        print(f"Duration: {duration:.1f}s | Dry Run: {self.config.dry_run}")
        print(f"-"*40)
        print(f"Companies found:   {self.results['companies_found']}")
        print(f"Companies updated: {self.results['companies_updated']}")
        print(f"Companies skipped: {self.results['companies_skipped']}")
        print(f"Properties set:    {self.results['properties_updated']}")
        print(f"Errors:            {self.results['errors']}")
        
        if self.errors:
            print(f"\nErrors:")
            for error in self.errors[:5]:
                print(f"  - {error}")
            if len(self.errors) > 5:
                print(f"  ... and {len(self.errors) - 5} more")
        
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Sync analytics for linked HubSpot companies (refresh workflow)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes")
    parser.add_argument("--org-id", help="Update specific organization by ID")
    parser.add_argument("--limit", type=int, help="Limit companies to process")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    
    args = parser.parse_args()
    
    try:
        config = Config.from_env()
    except KeyError as e:
        print(f"Missing required environment variable: {e}")
        sys.exit(1)
    
    if args.dry_run:
        config.dry_run = True
    
    orchestrator = AnalyticsSyncOrchestrator(config)
    results = orchestrator.run(
        org_id=args.org_id,
        limit=args.limit,
        verbose=not args.quiet,
    )
    
    if results["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
