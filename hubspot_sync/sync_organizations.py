#!/usr/bin/env python3
"""
Organization Sync Entry Point.

Syncs platform organizations TO HubSpot:
- Matches organizations to existing companies
- Creates placeholder companies for unmatched orgs
- Links orgs to companies via platform_organization_id
- Creates/associates contacts
- Creates tasks for manual review

This is the "discovery" workflow that runs periodically to onboard new platform
organizations into HubSpot CRM.

Usage:
    python sync_organizations.py                  # Full sync
    python sync_organizations.py --dry-run        # Preview changes
    python sync_organizations.py --org-id UUID    # Sync specific org
    python sync_organizations.py --limit 50       # Limit orgs processed
"""

import argparse
import re
import sys
import traceback
from datetime import datetime, timezone
from typing import Optional

import requests

# Load .env file automatically
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from .config import Config
from .filter_config import is_org_blacklisted, is_org_internal, get_spam_reason
from .clients.platform import PlatformClient, Organization
from .clients.hubspot import HubSpotClient
from .matching.matcher import Matcher, MatchResult, MatchType
from .matching.signals import SignalType
from .actions.linker import Linker
from .actions.contact_sync import ContactSyncer
from .actions.task_creator import TaskCreator
from .actions.company_creator import CompanyCreator
from .analytics.billing_status import BillingStatusComputer
from .utils.audit import AuditLog, SyncEventType


class OrganizationSyncOrchestrator:
    """
    Orchestrator for syncing platform organizations to HubSpot.
    
    This is the "discovery" workflow:
    - Fetches all organizations from platform DB
    - Matches each to a HubSpot company
    - Creates companies/contacts/tasks as needed
    
    Does NOT compute analytics - use sync_analytics.py for that.
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.audit_log = AuditLog()
        
        # Initialize clients
        self.platform = PlatformClient(config.db_config)
        self.hubspot = HubSpotClient(
            config.hubspot_api_key,
            config.hubspot_platform_org_id_property,
        )
        
        # Initialize Paddle Billing API if configured
        self.billing_computer = None
        if config.paddle_api_key and config.paddle_vendor_id:
            self.billing_computer = BillingStatusComputer(config.paddle_vendor_id, config.paddle_api_key)
        
        # Billing status cache
        self._billing_cache: dict[str, bool] = {}
        
        # Track companies claimed during this sync run
        self._claimed_companies: dict[str, str] = {}
        
        # Pre-fetched linked org_id → Company mapping (populated at start of run)
        self._linked_orgs: dict[str, object] = {}
        
        # Initialize action components
        self.matcher = Matcher(self.hubspot, config, self.billing_computer)
        self.linker = Linker(self.hubspot, config, self.audit_log, self.billing_computer)
        self.contact_syncer = ContactSyncer(self.hubspot, config, self.audit_log)
        self.task_creator = TaskCreator(self.hubspot, config, self.audit_log)
        self.company_creator = CompanyCreator(self.hubspot, config, self.audit_log, self.billing_computer)
        
        # Results tracking
        self.results = {
            "orgs_processed": 0,
            "orgs_skipped_blacklist": 0,
            "orgs_skipped_internal": 0,
            "already_linked": 0,
            "auto_linked": 0,
            "companies_created": 0,
            "companies_enriched": 0,
            "tasks_created": 0,
            "contacts_created": 0,
            "contacts_associated": 0,
            "no_match": 0,
            "conflicts": 0,
            "errors": 0,
        }
        self.errors: list[str] = []
    
    def run(
        self,
        org_id: Optional[str] = None,
        limit: Optional[int] = None,
        batch: Optional[tuple[int, int]] = None,
    ) -> dict:
        """
        Run the organization sync.
        
        Args:
            org_id: Optional specific organization ID to sync
            limit: Optional limit on number of organizations to process
            batch: Optional (k, n) tuple for batch processing.
                   k is the 1-indexed batch number, n is total batches.
                   Orgs are split by sorted ID so each batch is deterministic.
        """
        self.audit_log.start_sync_run()
        start_time = datetime.now(timezone.utc)
        self._claimed_companies = {}
        
        print(f"{'='*60}")
        print(f"Organization Sync Started")
        print(f"Run ID: {self.audit_log.sync_run_id}")
        print(f"Dry Run: {self.config.dry_run}")
        if batch:
            print(f"Batch: {batch[0]}/{batch[1]}")
        if limit:
            print(f"Limit: {limit} organizations")
        if not self.billing_computer:
            print(f"⚠️  Paddle not configured - billing_status will default to 'not started'")
        print(f"{'='*60}")
        
        try:
            # Fetch organizations
            if org_id:
                org = self.platform.get_organization_by_id(org_id)
                organizations = [org] if org else []
                if not org:
                    print(f"Organization {org_id} not found")
            else:
                organizations = self.platform.get_all_organizations()
            
            total_count = len(organizations)
            
            # Apply batch filtering (deterministic split by sorted org ID)
            if batch and not org_id:
                k, n = batch
                # Orgs come sorted by ID from the DB query, ensuring
                # each org always falls in the same batch
                chunk_size = len(organizations) // n
                remainder = len(organizations) % n
                
                # Distribute remainder across first batches so sizes differ by at most 1
                start = sum(chunk_size + (1 if i < remainder else 0) for i in range(k - 1))
                end = start + chunk_size + (1 if (k - 1) < remainder else 0)
                
                organizations = organizations[start:end]
                print(f"\nBatch {k}/{n}: processing orgs {start + 1}-{end} of {total_count} total\n")
            
            if limit and len(organizations) > limit:
                organizations = organizations[:limit]
                print(f"\nFetched {total_count} organizations, processing first {limit}\n")
            elif not batch:
                print(f"\nFetched {len(organizations)} organizations to process\n")
            
            # Pre-fetch which orgs are already linked in HubSpot (saves individual lookups)
            if not org_id:
                self._prefetch_linked_orgs()
            
            # Pre-fetch billing statuses
            self._prefetch_billing_statuses(organizations)
            
            # Process each organization
            for i, org in enumerate(organizations, 1):
                admin_email = org.admin_email or "no admin"
                print(f"\n[{i}/{len(organizations)}] {org.name}")
                print(f"  Org ID: {org.id}")
                print(f"  Admin: {admin_email}")
                try:
                    self._process_organization(org)
                except Exception as e:
                    error_msg = f"Error processing {org.name}: {str(e)}"
                    print(f"  ERROR: {error_msg}")
                    traceback.print_exc()
                    self.errors.append(error_msg)
                    self.results["errors"] += 1
                print()
            
            self.audit_log.save()
            
        except Exception as e:
            error_msg = f"Fatal error: {str(e)}"
            print(f"\n{error_msg}")
            traceback.print_exc()
            self.errors.append(error_msg)
            self.results["errors"] += 1
        
        finally:
            self.platform.close()
        
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        self._print_report(duration)
        
        return self.results
    
    def _prefetch_linked_orgs(self):
        """
        Pre-fetch all HubSpot companies that already have a platform_org_id.
        
        Builds a dict mapping org_id → Company so we can skip the expensive
        per-org signal collection for already-linked orgs. This replaces N
        individual API calls with a single paginated search.
        """
        print(f"Pre-fetching linked companies from HubSpot...")
        try:
            companies = self.hubspot.get_all_companies_with_platform_org_id()
            for company in companies:
                if company.platform_org_id:
                    self._linked_orgs[company.platform_org_id] = company
            print(f"  {len(self._linked_orgs)} companies already linked\n")
        except Exception as e:
            print(f"  Warning: Could not pre-fetch linked companies: {e}")
            print(f"  Falling back to per-org lookups\n")
            self._linked_orgs = {}
    
    def _prefetch_billing_statuses(self, organizations: list[Organization]):
        """Pre-fetch billing statuses for all organizations."""
        if not self.billing_computer:
            return
        
        paddle_ids = [org.paddle_id for org in organizations if org.paddle_id]
        if not paddle_ids:
            return
        
        print(f"Fetching billing statuses for {len(paddle_ids)} organizations...")
        try:
            statuses = self.billing_computer.get_billing_status_batch(paddle_ids)
            for org in organizations:
                if org.paddle_id and org.paddle_id in statuses:
                    self._billing_cache[org.id] = statuses[org.paddle_id].has_active_subscription
                else:
                    self._billing_cache[org.id] = False
            
            active_count = sum(1 for v in self._billing_cache.values() if v)
            print(f"  {active_count} with active subscriptions\n")
        except Exception as e:
            print(f"  Warning: Could not fetch billing statuses: {e}\n")
    
    def _get_subscription_status(self, org: Organization) -> bool:
        return self._billing_cache.get(org.id, False)
    
    def _print_match_details(self, match_result: MatchResult, org: Organization):
        """Print detailed match information."""
        company = match_result.matched_company
        company_display = "None"
        if company:
            company_display = company.name or f"Company #{company.id}"
            if company.domain:
                company_display += f" ({company.domain})"
        
        print(f"  Match: {match_result.match_type.value} -> {company_display}")
        print(f"    Confidence: {match_result.confidence:.0%}")
        
        if match_result.match_type == MatchType.NO_MATCH:
            spam_reason = get_spam_reason(org.admin_email)
            if spam_reason:
                print(f"    ⚠️  Likely spam: {spam_reason}")
        
        if match_result.candidates:
            top_candidate = match_result.candidates[0]
            for signal in top_candidate.signals:
                signal_name = signal.signal_type.value.replace("_", " ").title()
                
                if signal.signal_type == SignalType.DOMAIN_MATCH:
                    domain = signal.details.get("matched_domain", "?")
                    is_admin = signal.details.get("is_admin_domain", False)
                    admin_marker = " (admin)" if is_admin else ""
                    print(f"    - {signal_name}: {domain}{admin_marker}")
                elif signal.signal_type == SignalType.CONTACT_ASSOCIATION:
                    matched = signal.details.get("matched_count", 0)
                    total = signal.details.get("total_users", 0)
                    emails = signal.details.get("matched_emails", [])
                    email_preview = ", ".join(emails[:2])
                    if len(emails) > 2:
                        email_preview += f" +{len(emails)-2} more"
                    print(f"    - {signal_name}: {matched}/{total} users ({email_preview})")
                elif signal.signal_type == SignalType.PADDLE_NAME_MATCH:
                    paddle_name = signal.details.get("paddle_company_name", "?")
                    print(f"    - {signal_name}: '{paddle_name}'")
                elif signal.signal_type == SignalType.EXISTING_PLATFORM_ID:
                    print(f"    - Already linked (ground truth)")
                else:
                    print(f"    - {signal_name}: {signal.source}")
    
    def _process_organization(self, org: Organization):
        """Process a single organization."""
        # Check blacklist
        if is_org_blacklisted(org.id):
            print(f"  Skipping: blacklisted")
            self.results["orgs_skipped_blacklist"] += 1
            return
        
        # Check if internal
        user_emails = [u.email for u in org.users if u.email]
        if is_org_internal(org.admin_email, user_emails):
            print(f"  Skipping: internal organization")
            self.results["orgs_skipped_internal"] += 1
            return
        
        self.results["orgs_processed"] += 1
        
        if not org.users:
            print(f"  Skipping: no users")
            return
        
        # Fast path: if pre-fetched data shows this org is already linked,
        # skip the expensive signal collection and just sync contacts
        pre_linked_company = self._linked_orgs.get(org.id)
        if pre_linked_company:
            self._handle_already_linked_fast(org, pre_linked_company)
            return
        
        # Full matching pipeline for unlinked orgs
        match_result = self.matcher.match_organization(org)
        self._print_match_details(match_result, org)
        
        has_subscription = self._get_subscription_status(org)
        
        # Handle based on match type
        if match_result.match_type == MatchType.ALREADY_LINKED:
            self._handle_already_linked(org, match_result, has_subscription)
        elif match_result.match_type == MatchType.AUTO_LINK:
            self._handle_auto_link(org, match_result, has_subscription)
        elif match_result.match_type == MatchType.CONFLICT:
            self._handle_conflict(org, match_result, has_subscription)
        elif match_result.match_type == MatchType.MULTIPLE_MATCHES:
            self._handle_multiple_matches(org, match_result, has_subscription)
        elif match_result.match_type == MatchType.NEEDS_REVIEW:
            self._handle_needs_review(org, match_result, has_subscription)
        elif match_result.match_type == MatchType.NO_MATCH:
            self._handle_no_match(org, match_result, has_subscription)
    
    def _handle_already_linked_fast(self, org: Organization, company):
        """Fast path for orgs already linked (pre-fetched).
        
        Skips the entire matching pipeline (signal collection, scoring, etc.).
        Contacts are NOT synced here — that happens in sync_analytics.py
        which is the refresh workflow for already-linked companies.
        """
        self.results["already_linked"] += 1
        
        company_display = company.name or f"Company #{company.id}"
        if company.domain:
            company_display += f" ({company.domain})"
        print(f"  Already linked -> {company_display}")
        
        self._claimed_companies[company.id] = org.id
    
    def _handle_already_linked(self, org: Organization, match_result: MatchResult, has_subscription: bool):
        """Handle already linked organization (full matching path, fallback).
        
        Contacts are NOT synced here — that happens in sync_analytics.py.
        """
        self.results["already_linked"] += 1
        company = match_result.matched_company
        
        if company:
            self._claimed_companies[company.id] = org.id
            
            # Try to enrich if we now have Paddle data
            if self.config.auto_create_companies:
                enrich_result = self.company_creator.create_or_enrich_company(org, has_subscription)
                if enrich_result.was_enriched:
                    self.results["companies_enriched"] += 1
                    print(f"  Enriched existing company with Paddle data")
    
    def _handle_auto_link(self, org: Organization, match_result: MatchResult, has_subscription: bool):
        """Handle auto-link match."""
        company = match_result.matched_company
        company_id = company.id if company else None
        
        # Check for same-run conflict
        if company_id and company_id in self._claimed_companies:
            claiming_org_id = self._claimed_companies[company_id]
            print(f"    -> Same-run conflict: Company already claimed by org {claiming_org_id[:8]}...")
            
            self.results["conflicts"] += 1
            if self.config.auto_create_companies:
                create_result = self.company_creator.create_or_enrich_company(
                    org, has_subscription, has_real_usage=False
                )
                if create_result.was_created:
                    self.results["companies_created"] += 1
                    print(f"    -> Created separate placeholder company")
                    if create_result.company:
                        self._sync_contacts(org, create_result.company)
            
            task_result = self.task_creator.create_task_for_match_result(match_result)
            if task_result.success and not task_result.skipped:
                self.results["tasks_created"] += 1
                print(f"    -> Created task for SDR to resolve duplicate")
        else:
            # Normal auto-link
            link_result = self.linker.link_organization_to_company(org, company)
            if link_result.success:
                self.results["auto_linked"] += 1
                if company_id:
                    self._claimed_companies[company_id] = org.id
                print(f"    -> Linked successfully")
                self._sync_contacts(org, company)
            else:
                self.results["errors"] += 1
                self.errors.append(link_result.message)
                print(f"    -> Link failed: {link_result.message}")
    
    def _handle_conflict(self, org: Organization, match_result: MatchResult, has_subscription: bool):
        """Handle conflict (company already linked to different org)."""
        self.results["conflicts"] += 1
        print(f"    -> Conflict: company already linked to different org")
        
        if self.config.auto_create_companies:
            create_result = self.company_creator.create_or_enrich_company(
                org, has_subscription, has_real_usage=False
            )
            if create_result.was_created:
                self.results["companies_created"] += 1
                print(f"    -> Created separate placeholder company")
                if create_result.company:
                    self._sync_contacts(org, create_result.company)
        
        task_result = self.task_creator.create_task_for_match_result(match_result)
        if task_result.success and not task_result.skipped:
            self.results["tasks_created"] += 1
            print(f"    -> Created task for SDR to resolve duplicate")
    
    def _handle_multiple_matches(self, org: Organization, match_result: MatchResult, has_subscription: bool):
        """Handle multiple matches requiring review.
        
        When auto_create_companies is enabled, a placeholder company is created
        so the reviewer never has to create one manually — they only need to verify
        whether one of the existing candidates is a better fit.
        """
        placeholder_company = None
        
        if self.config.auto_create_companies:
            has_real_usage = False  # TODO: check from analytics
            
            create_result = self.company_creator.create_or_enrich_company(
                org, has_subscription, has_real_usage
            )
            if create_result.success:
                if create_result.was_created:
                    self.results["companies_created"] += 1
                    print(f"    -> Created placeholder company (reviewer can merge if needed)")
                if create_result.was_enriched:
                    self.results["companies_enriched"] += 1
                if create_result.company:
                    placeholder_company = create_result.company
                    self._sync_contacts(org, create_result.company)
            else:
                self.results["errors"] += 1
                self.errors.append(create_result.message)
                print(f"    -> ERROR creating placeholder: {create_result.message}")
        
        # Always create a review task (with or without placeholder)
        placeholder_id = placeholder_company.id if placeholder_company else None
        task_result = self.task_creator.create_task_for_match_result(
            match_result,
            placeholder_created=placeholder_company is not None,
            placeholder_company_id=placeholder_id,
        )
        if task_result.success and not task_result.skipped:
            self.results["tasks_created"] += 1
            print(f"  Created multiple-matches task")
        elif not task_result.success:
            self.results["errors"] += 1
            self.errors.append(f"Task creation failed for {org.name}: {task_result.message}")
            print(f"    -> ERROR: {task_result.message}")
    
    def _handle_needs_review(self, org: Organization, match_result: MatchResult, has_subscription: bool):
        """Handle match needing manual review.
        
        When auto_create_companies is enabled, a placeholder company is created
        so the org is never left unlinked. The reviewer only needs to verify
        whether the suggested match is correct and merge if so.
        """
        placeholder_company = None
        
        if self.config.auto_create_companies:
            has_real_usage = False  # TODO: check from analytics
            create_result = self.company_creator.create_or_enrich_company(
                org, has_subscription, has_real_usage
            )
            if create_result.success:
                if create_result.was_created:
                    self.results["companies_created"] += 1
                    print(f"    -> Created placeholder company (reviewer can merge if match is correct)")
                if create_result.was_enriched:
                    self.results["companies_enriched"] += 1
                if create_result.company:
                    placeholder_company = create_result.company
                    self._sync_contacts(org, create_result.company)
            else:
                self.results["errors"] += 1
                self.errors.append(create_result.message)
                print(f"    -> ERROR creating placeholder: {create_result.message}")
        
        # Always create a review task (with or without placeholder)
        placeholder_id = placeholder_company.id if placeholder_company else None
        task_result = self.task_creator.create_task_for_match_result(
            match_result,
            placeholder_created=placeholder_company is not None,
            placeholder_company_id=placeholder_id,
        )
        if task_result.success and not task_result.skipped:
            self.results["tasks_created"] += 1
            print(f"  Created review task")
        elif not task_result.success:
            self.results["errors"] += 1
            self.errors.append(f"Task creation failed for {org.name}: {task_result.message}")
            print(f"    -> ERROR: {task_result.message}")
    
    def _handle_no_match(self, org: Organization, match_result: MatchResult, has_subscription: bool):
        """Handle no match found."""
        self.results["no_match"] += 1
        
        if self.config.auto_create_companies:
            has_real_usage = False  # TODO: check from analytics
            create_result = self.company_creator.create_or_enrich_company(
                org, has_subscription, has_real_usage
            )
            if create_result.success:
                if create_result.was_created:
                    self.results["companies_created"] += 1
                    spam_info = " [LIKELY SPAM]" if "SPAM" in create_result.message else ""
                    print(f"    -> Created placeholder company{spam_info}")
                if create_result.was_enriched:
                    self.results["companies_enriched"] += 1
                    print(f"    -> Enriched company with Paddle data")
                if create_result.company:
                    self._sync_contacts(org, create_result.company)
            else:
                self.results["errors"] += 1
                self.errors.append(create_result.message)
                print(f"    -> ERROR creating company: {create_result.message}")
        else:
            if len(org.users) >= 2:
                task_result = self.task_creator.create_task_for_match_result(match_result)
                if task_result.success and not task_result.skipped:
                    self.results["tasks_created"] += 1
    
    def _sync_contacts(self, org: Organization, company):
        """Sync contacts for organization."""
        contact_result = self.contact_syncer.sync_organization_contacts(org, company)
        
        created = len(contact_result.contacts_created)
        associated = len(contact_result.contacts_associated)
        
        self.results["contacts_created"] += created
        self.results["contacts_associated"] += associated
        
        if created or associated:
            print(f"  Contacts: {created} created, {associated} associated")
        
        if contact_result.errors:
            for error in contact_result.errors:
                self.errors.append(error)
                print(f"  ERROR (contact sync): {error}")
            self.results["errors"] += len(contact_result.errors)
    
    def _print_report(self, duration: float):
        """Print summary report."""
        print(f"\n{'='*60}")
        print(f"ORGANIZATION SYNC COMPLETE")
        print(f"{'='*60}")
        print(f"Duration: {duration:.1f}s | Dry Run: {self.config.dry_run}")
        print(f"-"*40)
        print(f"Orgs processed:    {self.results['orgs_processed']}")
        print(f"Skipped (filter):  {self.results['orgs_skipped_blacklist'] + self.results['orgs_skipped_internal']}")
        print(f"Already linked:    {self.results['already_linked']}")
        print(f"Auto-linked:       {self.results['auto_linked']}")
        print(f"Companies created: {self.results['companies_created']}")
        print(f"Tasks created:     {self.results['tasks_created']}")
        print(f"Contacts created:  {self.results['contacts_created']}")
        print(f"Contacts assoc:    {self.results['contacts_associated']}")
        print(f"Conflicts:         {self.results['conflicts']}")
        print(f"Errors:            {self.results['errors']}")
        
        if self.errors:
            print(f"\nERRORS:")
            for error in self.errors[:10]:
                print(f"  - {error}")
            if len(self.errors) > 10:
                print(f"  ... and {len(self.errors) - 10} more")
        
        print(f"{'='*60}")


_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def _parse_uuid(value: str) -> str:
    """Validate and return a UUID string, giving a clear error on typos."""
    value = value.strip()
    if not _UUID_RE.match(value):
        import argparse as _argparse
        raise _argparse.ArgumentTypeError(
            f"Invalid UUID '{value}'. Expected format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (36 chars, got {len(value)})"
        )
    return value


def _parse_batch(value: str) -> tuple[int, int]:
    """
    Parse a batch argument like '2/5' into (k, n).
    
    Args:
        value: String in format 'K/N' where K is 1-indexed batch number
        
    Returns:
        Tuple of (k, n)
        
    Raises:
        argparse.ArgumentTypeError: If format is invalid
    """
    import argparse as _argparse
    try:
        parts = value.split("/")
        if len(parts) != 2:
            raise ValueError
        k, n = int(parts[0]), int(parts[1])
        if k < 1 or n < 1 or k > n:
            raise ValueError
        return (k, n)
    except ValueError:
        raise _argparse.ArgumentTypeError(
            f"Invalid batch format '{value}'. Use K/N where K is batch number (1-indexed) "
            f"and N is total batches. Example: --batch 2/5"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Sync platform organizations to HubSpot (linking workflow)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes")
    parser.add_argument("--org-id", type=_parse_uuid, help="Sync specific organization by ID")
    parser.add_argument("--limit", type=int, help="Limit organizations to process")
    parser.add_argument(
        "--batch", type=_parse_batch, metavar="K/N",
        help="Process batch K of N (e.g. --batch 1/5 for first 20%%). "
             "Splits orgs deterministically by ID so batches don't overlap."
    )
    
    args = parser.parse_args()
    
    try:
        config = Config.from_env()
    except KeyError as e:
        print(f"Missing required environment variable: {e}")
        sys.exit(1)
    
    if args.dry_run:
        config.dry_run = True
    
    orchestrator = OrganizationSyncOrchestrator(config)
    results = orchestrator.run(org_id=args.org_id, limit=args.limit, batch=args.batch)
    
    if results["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
