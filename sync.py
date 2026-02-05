#!/usr/bin/env python3
"""
HubSpot-Platform Sync Main Entry Point.

Orchestrates the sync process:
1. Fetches organizations from platform database
2. Matches them to HubSpot companies
3. Creates/associates contacts
4. Creates tasks for manual review
5. Reports results to Slack

Usage:
    python sync.py                  # Run full sync
    python sync.py --dry-run        # Preview changes without making them
    python sync.py --org-id UUID    # Sync specific organization
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional

import requests

from config import Config
from clients.platform import PlatformClient, Organization
from clients.hubspot import HubSpotClient
from clients.paddle import PaddleClient
from matching.matcher import Matcher, MatchResult, MatchType
from actions.linker import Linker
from actions.contact_sync import ContactSyncer
from actions.task_creator import TaskCreator
from actions.company_creator import CompanyCreator
from utils.audit import AuditLog, SyncEventType


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
        self.platform = PlatformClient(config.platform_db_url)
        self.hubspot = HubSpotClient(
            config.hubspot_api_key,
            config.hubspot_platform_org_id_property,
        )
        
        # Initialize Paddle if configured
        self.paddle = None
        if config.paddle_api_key and config.paddle_vendor_id:
            self.paddle = PaddleClient(config.paddle_vendor_id, config.paddle_api_key)
        
        # Initialize components
        self.matcher = Matcher(self.hubspot, config, self.paddle)
        self.linker = Linker(self.hubspot, config, self.audit_log)
        self.contact_syncer = ContactSyncer(self.hubspot, config, self.audit_log)
        self.task_creator = TaskCreator(self.hubspot, config, self.audit_log)
        self.company_creator = CompanyCreator(self.hubspot, config, self.audit_log, self.paddle)
        
        # Track results
        self.results = {
            "orgs_processed": 0,
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
    
    def run(self, org_id: Optional[str] = None) -> dict:
        """
        Run the sync process.
        
        Args:
            org_id: Optional specific organization ID to sync
            
        Returns:
            Dictionary with sync results
        """
        self.audit_log.start_sync_run()
        start_time = datetime.now(timezone.utc)
        
        print(f"{'='*60}")
        print(f"HubSpot-Platform Sync Started")
        print(f"Run ID: {self.audit_log.sync_run_id}")
        print(f"Dry Run: {self.config.dry_run}")
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
            
            print(f"\nFetched {len(organizations)} organizations to process\n")
            
            # Process each organization
            for i, org in enumerate(organizations, 1):
                print(f"[{i}/{len(organizations)}] Processing: {org.name} ({org.id})")
                try:
                    self._process_organization(org)
                except Exception as e:
                    error_msg = f"Error processing {org.name}: {str(e)}"
                    print(f"  ERROR: {error_msg}")
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
    
    def _process_organization(self, org: Organization):
        """Process a single organization."""
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
        print(f"  Match result: {match_result.match_type.value} - {match_result.message}")
        
        # Handle based on match type
        if match_result.match_type == MatchType.ALREADY_LINKED:
            self.results["already_linked"] += 1
            # Try to enrich if it's a placeholder and we now have Paddle data
            if self.config.auto_create_companies and match_result.matched_company:
                enrich_result = self.company_creator.create_or_enrich_company(org)
                if enrich_result.was_enriched:
                    self.results["companies_enriched"] += 1
                    print(f"  Enriched existing company with Paddle data")
            # Sync contacts even if already linked
            if match_result.matched_company:
                self._sync_contacts(org, match_result.matched_company)
        
        elif match_result.match_type == MatchType.AUTO_LINK:
            # Link the company
            link_result = self.linker.link_organization_to_company(
                org, match_result.matched_company
            )
            if link_result.success:
                self.results["auto_linked"] += 1
                print(f"  Linked to: {match_result.matched_company.name}")
                # Sync contacts after linking
                self._sync_contacts(org, match_result.matched_company)
            else:
                self.results["errors"] += 1
                self.errors.append(link_result.message)
        
        elif match_result.match_type == MatchType.CONFLICT:
            self.results["conflicts"] += 1
            # Create task for conflict
            task_result = self.task_creator.create_task_for_match_result(match_result)
            if task_result.success and not task_result.skipped:
                self.results["tasks_created"] += 1
                print(f"  Created conflict task")
        
        elif match_result.match_type == MatchType.MULTIPLE_MATCHES:
            # Create task for multiple matches
            task_result = self.task_creator.create_task_for_match_result(match_result)
            if task_result.success and not task_result.skipped:
                self.results["tasks_created"] += 1
                print(f"  Created multiple-matches task")
        
        elif match_result.match_type == MatchType.NEEDS_REVIEW:
            # Create task for review
            task_result = self.task_creator.create_task_for_match_result(match_result)
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
                create_result = self.company_creator.create_or_enrich_company(org)
                if create_result.success:
                    if create_result.was_created:
                        self.results["companies_created"] += 1
                        print(f"  Created placeholder company")
                    if create_result.was_enriched:
                        self.results["companies_enriched"] += 1
                        print(f"  Enriched company with Paddle data")
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
    
    def _sync_contacts(self, org: Organization, company):
        """Sync contacts for an organization."""
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
            f"Already linked:           {self.results['already_linked']}",
            f"Auto-linked:              {self.results['auto_linked']}",
            f"Companies created:        {self.results['companies_created']}",
            f"Companies enriched:       {self.results['companies_enriched']}",
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
                    {"type": "mrkdwn", "text": f"*Companies enriched:* {self.results['companies_enriched']}"},
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
    results = orchestrator.run(org_id=args.org_id)
    
    # Exit with error code if there were errors
    if results["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
