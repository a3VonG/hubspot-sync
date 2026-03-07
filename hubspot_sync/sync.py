#!/usr/bin/env python3
"""
HubSpot-Platform Sync - Combined Entry Point (Legacy).

This script runs BOTH organization sync AND analytics sync together.
For production use, prefer the separated entry points:

    python sync_organizations.py    # Link orgs to companies (daily/weekly)
    python sync_analytics.py        # Update analytics (hourly/daily)

See ARCHITECTURE.md for system overview.

Usage:
    python sync.py                  # Run full sync
    python sync.py --dry-run        # Preview changes without making them
    python sync.py --org-id UUID    # Sync specific organization
"""

import argparse
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
    pass  # dotenv not installed, use system env vars

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
from .actions.analytics_sync import AnalyticsSyncer
from .analytics.platform_analytics import PlatformAnalyticsComputer
from .analytics.billing_status import BillingStatusComputer
from .utils.audit import AuditLog, SyncEventType


class SyncOrchestrator:
    """
    Main orchestrator for the HubSpot-Platform sync.
    
    Coordinates all components and manages the sync workflow.
    """
    
    def __init__(self, config: Config):
        """
        Initialize the orchestrator.
        
        Args:
            config: Configuration object
        """
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
        
        # Billing status cache (org_id -> has_active_subscription)
        self._billing_cache: dict[str, bool] = {}
        
        # Track companies claimed during this sync run to prevent double-linking
        # Maps company_id -> org_id that claimed it
        self._claimed_companies: dict[str, str] = {}
        
        # Initialize components
        self.matcher = Matcher(self.hubspot, config, self.billing_computer)
        self.linker = Linker(self.hubspot, config, self.audit_log, self.billing_computer)
        self.contact_syncer = ContactSyncer(self.hubspot, config, self.audit_log)
        self.task_creator = TaskCreator(self.hubspot, config, self.audit_log)
        self.company_creator = CompanyCreator(self.hubspot, config, self.audit_log, self.billing_computer)
        
        # Initialize analytics computer and syncer
        self.analytics_computer = PlatformAnalyticsComputer(
            config.db_config,
            config,
            config.paddle_vendor_id,
            config.paddle_api_key,
        )
        self.analytics_syncer = AnalyticsSyncer(
            self.hubspot, self.analytics_computer, config, self.audit_log,
            billing_computer=self.billing_computer,
        )
        
        # Track results
        self.results = {
            "orgs_processed": 0,
            "orgs_skipped_blacklist": 0,
            "orgs_skipped_internal": 0,
            "already_linked": 0,
            "auto_linked": 0,
            "companies_created": 0,
            "companies_enriched": 0,
            "analytics_updated": 0,
            "tasks_created": 0,
            "contacts_created": 0,
            "contacts_associated": 0,
            "no_match": 0,
            "conflicts": 0,
            "errors": 0,
        }
        self.errors: list[str] = []
    
    def run(self, org_id: Optional[str] = None, limit: Optional[int] = None) -> dict:
        """
        Run the sync process.
        
        Args:
            org_id: Optional specific organization ID to sync
            limit: Optional limit on number of organizations to process
            
        Returns:
            Dictionary with sync results
        """
        self.audit_log.start_sync_run()
        start_time = datetime.now(timezone.utc)
        
        # Reset tracking for this run
        self._claimed_companies = {}
        
        print(f"{'='*60}")
        print(f"HubSpot-Platform Sync Started")
        print(f"Run ID: {self.audit_log.sync_run_id}")
        print(f"Dry Run: {self.config.dry_run}")
        if limit:
            print(f"Limit: {limit} organizations")
        if not self.billing_computer:
            print(f"⚠️  Paddle credentials not configured - billing_status will default to 'not started'")
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
            
            # Apply limit if specified
            if limit and len(organizations) > limit:
                organizations = organizations[:limit]
                print(f"\nFetched {total_count} organizations, processing first {limit}\n")
            else:
                print(f"\nFetched {len(organizations)} organizations to process\n")
            
            # Pre-fetch billing statuses for efficiency
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
                    self.audit_log.log(
                        SyncEventType.ERROR,
                        message=error_msg,
                        platform_org_id=org.id,
                        platform_org_name=org.name,
                    )
                print()
            
            # Save audit log
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
        
        # Generate and print report
        report = self._generate_report(duration)
        print(report)
        
        # Send to Slack if configured
        if self.config.slack_webhook_url:
            self._send_to_slack(report)
        
        return self.results
    
    def _prefetch_billing_statuses(self, organizations: list[Organization]):
        """
        Pre-fetch billing statuses for all organizations.
        
        Caches results to avoid individual API calls per organization.
        """
        if not self.billing_computer:
            return
        
        # Collect paddle_ids
        paddle_ids = [org.paddle_id for org in organizations if org.paddle_id]
        
        if not paddle_ids:
            return
        
        print(f"Fetching billing statuses for {len(paddle_ids)} organizations...")
        
        try:
            statuses = self.billing_computer.get_billing_status_batch(paddle_ids)
            
            # Build cache keyed by org_id
            for org in organizations:
                if org.paddle_id and org.paddle_id in statuses:
                    self._billing_cache[org.id] = statuses[org.paddle_id].has_active_subscription
                else:
                    self._billing_cache[org.id] = False
            
            active_count = sum(1 for v in self._billing_cache.values() if v)
            print(f"  {active_count} with active subscriptions, {len(self._billing_cache) - active_count} in testing\n")
        except Exception as e:
            print(f"  Warning: Could not fetch billing statuses: {e}\n")
    
    def _get_subscription_status(self, org: Organization) -> bool:
        """Get cached subscription status for an organization."""
        return self._billing_cache.get(org.id, False)
    
    def _print_match_details(self, match_result: MatchResult, org: Organization):
        """Print detailed match information for debugging/transparency."""
        company = match_result.matched_company
        company_display = "None"
        if company:
            company_display = company.name or f"Company #{company.id}"
            if company.domain:
                company_display += f" ({company.domain})"
        
        print(f"  Match: {match_result.match_type.value} -> {company_display}")
        print(f"    Confidence: {match_result.confidence:.0%}")
        
        # Show spam warning if applicable (no match and looks like spam)
        if match_result.match_type == MatchType.NO_MATCH:
            spam_reason = get_spam_reason(org.admin_email)
            if spam_reason:
                print(f"    ⚠️  Likely spam: {spam_reason}")
        
        # Show matching signals with details
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
        # Check blacklist first
        if is_org_blacklisted(org.id):
            print(f"  Skipping: blacklisted")
            self.results["orgs_skipped_blacklist"] += 1
            self.audit_log.log(
                SyncEventType.SKIPPED,
                message="Organization is blacklisted",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return
        
        # Check if internal/test organization (all emails from blacklisted domains)
        user_emails = [u.email for u in org.users if u.email]
        if is_org_internal(org.admin_email, user_emails):
            print(f"  Skipping: internal organization")
            self.results["orgs_skipped_internal"] += 1
            self.audit_log.log(
                SyncEventType.SKIPPED,
                message="Internal organization (blacklisted email domain)",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return
        
        self.results["orgs_processed"] += 1
        
        self.audit_log.log(
            SyncEventType.ORG_PROCESSED,
            message=f"Processing {org.name}",
            platform_org_id=org.id,
            platform_org_name=org.name,
            details={"user_count": len(org.users)},
        )
        
        # Skip organizations with no users
        if not org.users:
            print(f"  Skipping: no users")
            self.audit_log.log(
                SyncEventType.SKIPPED,
                message="No users in organization",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return
        
        # Match organization to company
        match_result = self.matcher.match_organization(org)
        self._print_match_details(match_result, org)
        
        # Get subscription status for this org
        has_subscription = self._get_subscription_status(org)
        
        # Handle based on match type
        if match_result.match_type == MatchType.ALREADY_LINKED:
            self.results["already_linked"] += 1
            # Track this company as claimed by this org
            if match_result.matched_company:
                self._claimed_companies[match_result.matched_company.id] = org.id
            # Try to enrich if it's a placeholder and we now have Paddle data
            if self.config.auto_create_companies and match_result.matched_company:
                enrich_result = self.company_creator.create_or_enrich_company(
                    org, has_subscription
                )
                if enrich_result.was_enriched:
                    self.results["companies_enriched"] += 1
                    print(f"  Enriched existing company with Paddle data")
            # Sync contacts even if already linked
            if match_result.matched_company:
                self._sync_contacts(org, match_result.matched_company)
        
        elif match_result.match_type == MatchType.AUTO_LINK:
            company = match_result.matched_company
            company_id = company.id if company else None
            
            # Check if this company was already claimed by another org in this run
            if company_id and company_id in self._claimed_companies:
                claiming_org_id = self._claimed_companies[company_id]
                print(f"    -> Same-run conflict: Company already claimed by org {claiming_org_id[:8]}...")
                
                # Treat as conflict - create placeholder for this org
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
                
                # Create task for SDR
                task_result = self.task_creator.create_task_for_match_result(match_result)
                if task_result.success and not task_result.skipped:
                    self.results["tasks_created"] += 1
                    print(f"    -> Created task for SDR to resolve duplicate")
            else:
                # Normal auto-link flow
                link_result = self.linker.link_organization_to_company(org, company)
                if link_result.success:
                    self.results["auto_linked"] += 1
                    # Track this company as claimed
                    if company_id:
                        self._claimed_companies[company_id] = org.id
                    print(f"    -> Linked successfully")
                    # Sync contacts after linking
                    self._sync_contacts(org, company)
                else:
                    self.results["errors"] += 1
                    self.errors.append(link_result.message)
                    print(f"    -> Link failed: {link_result.message}")
        
        elif match_result.match_type == MatchType.CONFLICT:
            self.results["conflicts"] += 1
            # Create a placeholder company for this org to avoid recurring conflicts
            # The conflicting company already has a different org linked
            print(f"    -> Conflict: company already linked to different org")
            
            if self.config.auto_create_companies:
                # Create separate placeholder for this org
                create_result = self.company_creator.create_or_enrich_company(
                    org, has_subscription, has_real_usage=False  # TODO: check real usage
                )
                if create_result.was_created:
                    self.results["companies_created"] += 1
                    print(f"    -> Created separate placeholder company")
                    # Sync contacts to the new placeholder
                    if create_result.company:
                        self._sync_contacts(org, create_result.company)
            
            # Create task for SDR to resolve the duplicate
            task_result = self.task_creator.create_task_for_match_result(match_result)
            if task_result.success and not task_result.skipped:
                self.results["tasks_created"] += 1
                print(f"    -> Created task for SDR to resolve duplicate")
        
        elif match_result.match_type == MatchType.MULTIPLE_MATCHES:
            # Auto-create placeholder so the reviewer doesn't have to
            placeholder_company = None
            if self.config.auto_create_companies:
                create_result = self.company_creator.create_or_enrich_company(
                    org, has_subscription, has_real_usage=False
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
            
            # Create review task (with or without placeholder)
            placeholder_id = placeholder_company.id if placeholder_company else None
            task_result = self.task_creator.create_task_for_match_result(
                match_result,
                placeholder_created=placeholder_company is not None,
                placeholder_company_id=placeholder_id,
            )
            if task_result.success and not task_result.skipped:
                self.results["tasks_created"] += 1
                print(f"  Created multiple-matches task")
        
        elif match_result.match_type == MatchType.NEEDS_REVIEW:
            # Auto-create placeholder so the org is never left unlinked
            placeholder_company = None
            if self.config.auto_create_companies:
                create_result = self.company_creator.create_or_enrich_company(
                    org, has_subscription, has_real_usage=False
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
            
            # Create review task (with or without placeholder)
            placeholder_id = placeholder_company.id if placeholder_company else None
            task_result = self.task_creator.create_task_for_match_result(
                match_result,
                placeholder_created=placeholder_company is not None,
                placeholder_company_id=placeholder_id,
            )
            if task_result.success and not task_result.skipped:
                self.results["tasks_created"] += 1
                print(f"  Created review task")
        
        elif match_result.match_type == MatchType.NO_MATCH:
            self.results["no_match"] += 1
            self.audit_log.log(
                SyncEventType.NO_MATCH,
                message=match_result.message,
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            
            # Auto-create company if enabled
            if self.config.auto_create_companies:
                # TODO: Get real usage from analytics when available
                has_real_usage = False  
                create_result = self.company_creator.create_or_enrich_company(
                    org, has_subscription, has_real_usage
                )
                if create_result.success:
                    if create_result.was_created:
                        self.results["companies_created"] += 1
                        # Include spam flag info in output
                        spam_info = " [LIKELY SPAM]" if "SPAM" in create_result.message else ""
                        print(f"    -> Created placeholder company{spam_info}")
                    if create_result.was_enriched:
                        self.results["companies_enriched"] += 1
                        print(f"    -> Enriched company with Paddle data")
                    # Sync contacts to the new/existing company
                    if create_result.company:
                        self._sync_contacts(org, create_result.company)
                else:
                    self.results["errors"] += 1
                    self.errors.append(create_result.message)
            else:
                # Create task for no match (only for orgs with significant users)
                if len(org.users) >= 2:
                    task_result = self.task_creator.create_task_for_match_result(match_result)
                    if task_result.success and not task_result.skipped:
                        self.results["tasks_created"] += 1
    
    def _sync_contacts_and_analytics(self, org: Organization, company):
        """Sync contacts and analytics for an organization."""
        # Sync contacts
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
            self.results["errors"] += len(contact_result.errors)
        
        # Sync analytics
        analytics_result = self.analytics_syncer.sync_organization_analytics(
            org.id, org.paddle_id, company
        )
        
        if analytics_result.success:
            self.results["analytics_updated"] += 1
            print(f"  Analytics: {len(analytics_result.properties_updated)} properties updated")
        else:
            if "blacklisted" not in analytics_result.message.lower():
                self.results["errors"] += 1
                self.errors.append(f"Analytics sync failed for {org.name}: {analytics_result.message}")
    
    def _sync_contacts(self, org: Organization, company):
        """Sync contacts for an organization (backwards compatibility)."""
        self._sync_contacts_and_analytics(org, company)
    
    def _sync_contacts_only(self, org: Organization, company):
        """Sync only contacts, no analytics."""
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
            self.results["errors"] += len(contact_result.errors)
    
    def _generate_report(self, duration: float) -> str:
        """Generate a summary report."""
        lines = [
            "",
            "=" * 60,
            "SYNC COMPLETE",
            "=" * 60,
            "",
            f"Duration: {duration:.1f} seconds",
            f"Dry Run: {self.config.dry_run}",
            f"Auto-create companies: {self.config.auto_create_companies}",
            "",
            "SUMMARY",
            "-" * 40,
            f"Organizations processed:  {self.results['orgs_processed']}",
            f"Orgs skipped (blacklist): {self.results['orgs_skipped_blacklist']}",
            f"Orgs skipped (internal):  {self.results['orgs_skipped_internal']}",
            f"Already linked:           {self.results['already_linked']}",
            f"Auto-linked:              {self.results['auto_linked']}",
            f"Companies created:        {self.results['companies_created']}",
            f"Companies enriched:       {self.results['companies_enriched']}",
            f"Analytics updated:        {self.results['analytics_updated']}",
            f"Tasks created:            {self.results['tasks_created']}",
            f"No match found:           {self.results['no_match']}",
            f"Conflicts:                {self.results['conflicts']}",
            "",
            f"Contacts created:         {self.results['contacts_created']}",
            f"Contacts associated:      {self.results['contacts_associated']}",
            "",
            f"Errors:                   {self.results['errors']}",
        ]
        
        if self.errors:
            lines.extend([
                "",
                "ERRORS",
                "-" * 40,
            ])
            for error in self.errors[:10]:
                lines.append(f"  - {error}")
            if len(self.errors) > 10:
                lines.append(f"  ... and {len(self.errors) - 10} more errors")
        
        lines.append("")
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def _send_to_slack(self, report: str):
        """Send report to Slack webhook."""
        if not self.config.slack_webhook_url:
            return
        
        # Format for Slack
        status_emoji = "✅" if self.results["errors"] == 0 else "⚠️"
        dry_run_note = " [DRY RUN]" if self.config.dry_run else ""
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} HubSpot Sync Complete{dry_run_note}",
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Organizations:* {self.results['orgs_processed']}"},
                    {"type": "mrkdwn", "text": f"*Auto-linked:* {self.results['auto_linked']}"},
                    {"type": "mrkdwn", "text": f"*Already linked:* {self.results['already_linked']}"},
                    {"type": "mrkdwn", "text": f"*Companies created:* {self.results['companies_created']}"},
                    {"type": "mrkdwn", "text": f"*Analytics updated:* {self.results['analytics_updated']}"},
                    {"type": "mrkdwn", "text": f"*Tasks created:* {self.results['tasks_created']}"},
                    {"type": "mrkdwn", "text": f"*Contacts created:* {self.results['contacts_created']}"},
                    {"type": "mrkdwn", "text": f"*Contacts associated:* {self.results['contacts_associated']}"},
                ]
            },
        ]
        
        if self.results["errors"] > 0:
            error_text = "\n".join(f"• {e}" for e in self.errors[:5])
            if len(self.errors) > 5:
                error_text += f"\n... and {len(self.errors) - 5} more"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Errors ({self.results['errors']}):*\n{error_text}",
                }
            })
        
        if self.results["conflicts"] > 0:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"⚠️ *{self.results['conflicts']} conflicts* require manual resolution in HubSpot Tasks.",
                }
            })
        
        try:
            response = requests.post(
                self.config.slack_webhook_url,
                json={"blocks": blocks},
                timeout=10,
            )
            response.raise_for_status()
            print("Report sent to Slack")
        except Exception as e:
            print(f"Failed to send to Slack: {e}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync platform organizations with HubSpot companies"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them",
    )
    parser.add_argument(
        "--org-id",
        help="Sync a specific organization by ID",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the number of organizations to process",
    )
    
    args = parser.parse_args()
    
    # Load config from environment
    try:
        config = Config.from_env()
    except KeyError as e:
        print(f"Missing required environment variable: {e}")
        sys.exit(1)
    
    # Override dry run from command line
    if args.dry_run:
        config.dry_run = True
    
    # Run sync
    orchestrator = SyncOrchestrator(config)
    results = orchestrator.run(org_id=args.org_id, limit=args.limit)
    
    # Exit with error code if there were errors
    if results["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
