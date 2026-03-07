"""
HubSpot task creation for manual resolution.

Creates tasks in HubSpot for conflicts and situations
that require human review.
"""

from dataclasses import dataclass
from typing import Optional

from ..clients.hubspot import HubSpotClient, Task
from ..clients.platform import Organization
from ..config import Config
from ..matching.matcher import MatchResult, MatchType
from ..utils.audit import AuditLog, SyncEventType


@dataclass
class TaskResult:
    """Result of task creation."""
    success: bool
    task: Optional[Task] = None
    message: str = ""
    skipped: bool = False


class TaskCreator:
    """
    Creates HubSpot tasks for situations requiring manual review.
    
    Handles conflicts, low-confidence matches, and multiple match scenarios.
    
    Task subjects include a tag [ORG:<org_id>] to enable reliable duplicate detection.
    """
    
    # Prefix used in task subjects for duplicate detection
    ORG_TAG_FORMAT = "[ORG:{org_id}]"
    
    def __init__(
        self,
        hubspot: HubSpotClient,
        config: Config,
        audit_log: AuditLog,
    ):
        """
        Initialize the task creator.
        
        Args:
            hubspot: HubSpot API client
            config: Configuration
            audit_log: Audit logger
        """
        self.hubspot = hubspot
        self.config = config
        self.audit_log = audit_log
    
    def _make_org_tag(self, org_id: str) -> str:
        """Create the org tag for task subjects."""
        return self.ORG_TAG_FORMAT.format(org_id=org_id)
    
    def create_task_for_match_result(
        self,
        result: MatchResult,
        placeholder_created: bool = False,
        placeholder_company_id: Optional[str] = None,
    ) -> TaskResult:
        """
        Create a HubSpot task based on match result.
        
        Args:
            result: MatchResult from the matcher
            placeholder_created: Whether a placeholder company was auto-created
            placeholder_company_id: HubSpot ID of the placeholder company (to associate with task)
            
        Returns:
            TaskResult with outcome
        """
        if result.match_type == MatchType.CONFLICT:
            return self._create_conflict_task(result)
        elif result.match_type == MatchType.MULTIPLE_MATCHES:
            return self._create_multiple_matches_task(result, placeholder_created, placeholder_company_id)
        elif result.match_type == MatchType.NEEDS_REVIEW:
            return self._create_review_task(result, placeholder_created, placeholder_company_id)
        elif result.match_type == MatchType.NO_MATCH:
            return self._create_no_match_task(result)
        else:
            return TaskResult(
                success=True,
                skipped=True,
                message=f"No task needed for {result.match_type.value}",
            )
    
    def _admin_label(self, org: Organization) -> str:
        """Short label for the org: admin email or org name."""
        return org.admin_email or org.name
    
    @staticmethod
    def _collect_candidate_company_ids(result: MatchResult) -> list[str]:
        """Collect all unique company IDs from candidates and matched_company."""
        ids: list[str] = []
        seen: set[str] = set()
        
        # Add matched_company first (most relevant)
        if result.matched_company and result.matched_company.id:
            ids.append(result.matched_company.id)
            seen.add(result.matched_company.id)
        
        # Add all candidates
        for candidate in (result.candidates or []):
            cid = candidate.company.id
            if cid and cid not in seen:
                ids.append(cid)
                seen.add(cid)
        
        return ids
    
    def _create_conflict_task(self, result: MatchResult) -> TaskResult:
        """Create task for platform ID conflict."""
        org = result.organization
        company = result.matched_company
        
        tag = self._make_org_tag(org.id)
        company_name = company.name if company else "Unknown"
        subject = f"{tag} Link conflict: {self._admin_label(org)} → {company_name} (already linked to another org)"
        body = self._format_conflict_body(result)
        
        # Associate with all candidate companies so reviewer can see them
        all_ids = self._collect_candidate_company_ids(result)
        
        return self._create_task(
            subject=subject,
            body=body,
            org=org,
            company_ids=all_ids,
        )
    
    def _create_multiple_matches_task(
        self,
        result: MatchResult,
        placeholder_created: bool = False,
        placeholder_company_id: Optional[str] = None,
    ) -> TaskResult:
        """Create task for multiple possible company matches."""
        org = result.organization
        
        tag = self._make_org_tag(org.id)
        candidate_names = [c.company.name or c.company.domain or "?" for c in result.candidates[:3]]
        if placeholder_created:
            subject = f"{tag} Verify placeholder for {self._admin_label(org)} — merge with {' or '.join(candidate_names)}?"
        else:
            subject = f"{tag} Pick correct company for {self._admin_label(org)}: {' or '.join(candidate_names)}?"
        body = self._format_multiple_matches_body(result, placeholder_created)
        
        # Associate with ALL candidate companies + placeholder so reviewer can click through
        all_ids = self._collect_candidate_company_ids(result)
        if placeholder_company_id and placeholder_company_id not in all_ids:
            all_ids.insert(0, placeholder_company_id)  # placeholder first
        
        return self._create_task(
            subject=subject,
            body=body,
            org=org,
            company_ids=all_ids,
        )
    
    def _create_review_task(
        self,
        result: MatchResult,
        placeholder_created: bool = False,
        placeholder_company_id: Optional[str] = None,
    ) -> TaskResult:
        """Create task for medium-confidence match needing review."""
        org = result.organization
        company = result.matched_company
        
        tag = self._make_org_tag(org.id)
        company_name = company.name if company else "Unknown"
        confidence_pct = int(result.confidence * 100)
        if placeholder_created:
            subject = f"{tag} Verify placeholder for {self._admin_label(org)} — possible match: {company_name} ({confidence_pct}%)"
        else:
            subject = f"{tag} Verify match: {self._admin_label(org)} → {company_name} ({confidence_pct}% confidence)"
        body = self._format_review_body(result, placeholder_created)
        
        # Associate with all candidate companies + placeholder
        all_ids = self._collect_candidate_company_ids(result)
        if placeholder_company_id and placeholder_company_id not in all_ids:
            all_ids.insert(0, placeholder_company_id)  # placeholder first
        
        return self._create_task(
            subject=subject,
            body=body,
            org=org,
            company_ids=all_ids,
        )
    
    def _create_no_match_task(self, result: MatchResult) -> TaskResult:
        """Create task for organization with no company match."""
        org = result.organization
        
        # Only create task if org has users (active org)
        if not org.users:
            return TaskResult(
                success=True,
                skipped=True,
                message=f"No task for empty org {org.name}",
            )
        
        tag = self._make_org_tag(org.id)
        subject = f"{tag} No company found for {self._admin_label(org)} ({len(org.users)} users)"
        body = self._format_no_match_body(result)
        
        # Associate with any low-score candidates if present
        all_ids = self._collect_candidate_company_ids(result)
        
        return self._create_task(
            subject=subject,
            body=body,
            org=org,
            company_ids=all_ids if all_ids else None,
        )
    
    def _check_for_existing_task(self, org: Organization) -> Optional[Task]:
        """
        Check if an open task already exists for this organization.
        
        Searches for tasks containing the org tag [ORG:<org_id>] in the subject.
        Only considers tasks that are not completed.
        
        Args:
            org: The platform organization
            
        Returns:
            Existing Task if found, None otherwise
        """
        org_tag = self._make_org_tag(org.id)
        
        # Search for tasks containing the org ID
        existing_tasks = self.hubspot.search_tasks_by_subject(org.id)
        
        for task in existing_tasks:
            # Check if task contains our org tag in subject
            if org_tag in task.subject:
                # Skip completed tasks - allow creating new task if previous was resolved
                if task.status in ("COMPLETED", "CANCELLED"):
                    continue
                return task
            
            # Fallback: check if org.id appears in subject (for backwards compatibility)
            if org.id in task.subject:
                if task.status in ("COMPLETED", "CANCELLED"):
                    continue
                return task
        
        # Also check task body for org ID (in case subject was truncated)
        for task in existing_tasks:
            if task.body and org.id in task.body:
                if task.status in ("COMPLETED", "CANCELLED"):
                    continue
                return task
        
        return None
    
    def _create_task(
        self,
        subject: str,
        body: str,
        org: Organization,
        company_id: Optional[str] = None,
        company_ids: Optional[list[str]] = None,
    ) -> TaskResult:
        """Create the actual HubSpot task.
        
        Args:
            subject: Task subject line
            body: Task body text
            org: Platform organization
            company_id: Single company to associate (legacy, still supported)
            company_ids: List of companies to associate (all candidates)
        """
        print(f"  [Task] Checking for existing task for {org.name} ({org.id[:8]}...)")
        
        # Check for existing open task to avoid duplicates
        existing_task = self._check_for_existing_task(org)
        if existing_task:
            print(f"  [Task] Skipped: open task already exists: {existing_task.subject}")
            self.audit_log.log(
                SyncEventType.SKIPPED,
                message=f"Open task already exists for {org.name}: {existing_task.subject}",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return TaskResult(
                success=True,
                skipped=True,
                message=f"Task already exists: {existing_task.subject}",
            )
        
        if self.config.dry_run:
            print(f"  [Task] [DRY RUN] Would create: {subject}")
            self.audit_log.log(
                SyncEventType.TASK_CREATED,
                message=f"[DRY RUN] Would create task: {subject}",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return TaskResult(
                success=True,
                message=f"[DRY RUN] Would create: {subject}",
            )
        
        queue_id = self.config.task_queue_id
        if queue_id:
            print(f"  [Task] Creating task in queue {queue_id}: {subject}")
        else:
            print(f"  [Task] Creating task (no queue): {subject}")
        task = self.hubspot.create_task(
            subject=subject,
            body=body,
            associated_company_id=company_id,
            associated_company_ids=company_ids,
            queue_id=queue_id,
        )
        
        if task:
            print(f"  [Task] Created successfully (ID: {task.id})")
            self.audit_log.log(
                SyncEventType.TASK_CREATED,
                message=f"Created task: {subject}",
                platform_org_id=org.id,
                platform_org_name=org.name,
                hubspot_company_id=company_id,
            )
            return TaskResult(
                success=True,
                task=task,
                message=f"Created task: {subject}",
            )
        else:
            print(f"  [Task] ERROR: Failed to create task for {org.name}")
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to create task for {org.name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return TaskResult(
                success=False,
                message="Failed to create task",
            )
    
    def _format_org_section(self, org: Organization) -> list[str]:
        """Format the common org info section."""
        lines = [
            "WHO",
            f"  Admin email: {org.admin_email or 'N/A'}",
            f"  Org name: {org.name}",
            f"  Users: {len(org.users)}",
        ]
        for email in org.user_emails[:8]:
            lines.append(f"    - {email}")
        if len(org.user_emails) > 8:
            lines.append(f"    ... and {len(org.user_emails) - 8} more")
        if org.paddle_id:
            lines.append(f"  Paddle ID: {org.paddle_id}")
        lines.append(f"  Platform org ID: {org.id}")
        return lines

    def _format_candidate_line(self, match, index: int) -> str:
        """Format a single candidate company line."""
        company = match.company
        signals = ", ".join(s.signal_type.value for s in match.signals)
        domain_info = f" | domain: {company.domain}" if company.domain else ""
        return f"  {index}. {company.name or '(unnamed)'}{domain_info} | matched on: {signals} ({int(match.score * 100)}%)"

    def _format_conflict_body(self, result: MatchResult) -> str:
        """Format task body for conflict scenario."""
        org = result.organization
        company = result.matched_company
        existing_org_id = company.platform_org_id if company else "N/A"
        
        lines = self._format_org_section(org)
        lines.extend([
            "",
            "WHAT HAPPENED",
            f"  Our sync matched this org to \"{company.name if company else 'Unknown'}\" by domain,",
            f"  but that company is already linked to a different platform org ({existing_org_id}).",
            "  A placeholder company was created so this org isn't lost.",
            "",
            "CONFLICTING ORGS",
            f"  This org:    {org.id}  ({org.admin_email or org.name})",
            f"  Other org:   {existing_org_id}",
            "",
            "LIKELY CAUSES",
            "  - Same company signed up twice on the platform (merge the orgs)",
            "  - Two different companies share a domain (keep separate)",
            "  - Employee moved to a new org (update the link)",
            "",
            "WHAT TO DO",
            "  1. Check both platform orgs — are they the same business?",
            "",
            "  If YES (same business, duplicate signup):",
            "    a. Merge the two platform organizations (keep the active one)",
            "    b. Merge or delete the placeholder HubSpot company",
            "    c. The next sync will pick up the merged org automatically",
            "",
            "  If NO (different businesses sharing a domain):",
            "    → Keep both — the placeholder is correct, no action needed",
            "",
            "  If EMPLOYEE MOVED to a new org:",
            f"    a. Update \"{company.name if company else 'Unknown'}\" to point to the correct org",
            f"       (set \"{self.hubspot.platform_org_id_property}\" to the right org ID)",
            "    b. Delete the placeholder if no longer needed",
        ])
        
        return "\n".join(lines)
    
    def _format_multiple_matches_body(self, result: MatchResult, placeholder_created: bool = False) -> str:
        """Format task body for multiple matches scenario."""
        org = result.organization
        
        lines = self._format_org_section(org)
        
        if placeholder_created:
            lines.extend([
                "",
                "WHAT HAPPENED",
                "  The sync found multiple HubSpot companies that could belong to this org,",
                "  but none was a clear winner. A placeholder company was auto-created and",
                "  linked so the org doesn't fall through the cracks.",
                "",
                "EXISTING CANDIDATES (possible duplicates)",
            ])
        else:
            lines.extend([
                "",
                "WHAT HAPPENED",
                "  The sync found multiple HubSpot companies that could belong to this org.",
                "  It couldn't pick one automatically, so it needs your help.",
                "",
                "CANDIDATES (pick one)",
            ])
        
        for i, match in enumerate(result.candidates[:5], 1):
            lines.append(self._format_candidate_line(match, i))
        
        if len(result.candidates) > 5:
            lines.append(f"  ... and {len(result.candidates) - 5} more")
        
        if placeholder_created:
            lines.extend([
                "",
                "WHAT TO DO",
                "  If one of the candidates above IS the right company:",
                f"    1. Set its \"{self.hubspot.platform_org_id_property}\" property to: {org.id}",
                "    2. Delete or merge the auto-created placeholder company",
                "    3. The next sync run will link them automatically",
                "",
                "  If NONE of the candidates are correct:",
                "    → No action needed — the placeholder company is already linked.",
                "    → You can rename it or enrich it with the correct details.",
            ])
        else:
            lines.extend([
                "",
                "WHAT TO DO",
                "  1. Open the correct company in HubSpot",
                f"  2. Set its \"{self.hubspot.platform_org_id_property}\" property to: {org.id}",
                "  3. The next sync run will link them automatically",
                "  4. If none of these companies are correct, create a new one or ignore",
            ])
        
        return "\n".join(lines)
    
    def _format_review_body(self, result: MatchResult, placeholder_created: bool = False) -> str:
        """Format task body for review scenario."""
        org = result.organization
        company = result.matched_company
        confidence_pct = int(result.confidence * 100)
        
        lines = self._format_org_section(org)
        
        if placeholder_created:
            lines.extend([
                "",
                "WHAT HAPPENED",
                f"  The sync found a possible match ({confidence_pct}% confidence) but wasn't sure enough",
                "  to link automatically. A placeholder company was auto-created and linked so",
                "  the org isn't lost.",
                "",
                "SUGGESTED MATCH (possible duplicate)",
                f"  Company: {company.name if company else 'Unknown'}",
                f"  Domain: {company.domain if company else 'N/A'}",
            ])
        else:
            lines.extend([
                "",
                "WHAT HAPPENED",
                f"  The sync found a possible match but isn't confident enough to link automatically ({confidence_pct}%).",
                "",
                "SUGGESTED MATCH",
                f"  Company: {company.name if company else 'Unknown'}",
                f"  Domain: {company.domain if company else 'N/A'}",
            ])
        
        if result.candidates:
            signals = ", ".join(s.signal_type.value for s in result.candidates[0].signals)
            lines.append(f"  Matched on: {signals}")
        
        if placeholder_created:
            lines.extend([
                "",
                "WHAT TO DO",
                "  If the suggested match IS the right company:",
                f"    1. Set its \"{self.hubspot.platform_org_id_property}\" property to: {org.id}",
                "    2. Delete or merge the auto-created placeholder company",
                "    3. The next sync run will link them automatically",
                "",
                "  If the suggested match is WRONG:",
                "    → No action needed — the placeholder company is already linked.",
                "    → You can rename it or enrich it with the correct details.",
            ])
        else:
            lines.extend([
                "",
                "WHAT TO DO",
                "  If this is the right company:",
                f"    → Set its \"{self.hubspot.platform_org_id_property}\" property to: {org.id}",
                "  If it's wrong:",
                "    → Find or create the right company and set the property there instead",
            ])
        
        return "\n".join(lines)
    
    def _format_no_match_body(self, result: MatchResult) -> str:
        """Format task body for no match scenario."""
        org = result.organization
        
        lines = self._format_org_section(org)
        lines.extend([
            "",
            "WHAT HAPPENED",
            "  No HubSpot company matched this platform org (no domain, name, or Paddle match).",
            "",
            "WHAT TO DO",
            "  Option A: Find the company in HubSpot and set its",
            f"    \"{self.hubspot.platform_org_id_property}\" property to: {org.id}",
            "  Option B: Create a new company in HubSpot with that property set",
            "  Option C: Ignore if this org is not relevant (spam, test, etc.)",
        ])
        
        return "\n".join(lines)
