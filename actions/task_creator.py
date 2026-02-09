"""
HubSpot task creation for manual resolution.

Creates tasks in HubSpot for conflicts and situations
that require human review.
"""

from dataclasses import dataclass
from typing import Optional

from clients.hubspot import HubSpotClient, Task
from clients.platform import Organization
from config import Config
from matching.matcher import MatchResult, MatchType
from matching.scorer import ScoredMatch
from utils.audit import AuditLog, SyncEventType


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
    
    def create_task_for_match_result(self, result: MatchResult) -> TaskResult:
        """
        Create a HubSpot task based on match result.
        
        Args:
            result: MatchResult from the matcher
            
        Returns:
            TaskResult with outcome
        """
        if result.match_type == MatchType.CONFLICT:
            return self._create_conflict_task(result)
        elif result.match_type == MatchType.MULTIPLE_MATCHES:
            return self._create_multiple_matches_task(result)
        elif result.match_type == MatchType.NEEDS_REVIEW:
            return self._create_review_task(result)
        elif result.match_type == MatchType.NO_MATCH:
            return self._create_no_match_task(result)
        else:
            return TaskResult(
                success=True,
                skipped=True,
                message=f"No task needed for {result.match_type.value}",
            )
    
    def _create_conflict_task(self, result: MatchResult) -> TaskResult:
        """Create task for platform ID conflict."""
        org = result.organization
        company = result.matched_company
        
        tag = self._make_org_tag(org.id)
        subject = f"{tag} Sync Conflict: {org.name} claims company already linked"
        body = self._format_conflict_body(result)
        
        return self._create_task(
            subject=subject,
            body=body,
            org=org,
            company_id=company.id if company else None,
        )
    
    def _create_multiple_matches_task(self, result: MatchResult) -> TaskResult:
        """Create task for multiple possible company matches."""
        org = result.organization
        
        tag = self._make_org_tag(org.id)
        subject = f"{tag} Sync Review: Multiple companies match {org.name}"
        body = self._format_multiple_matches_body(result)
        
        # Associate with top candidate if available
        top_company_id = None
        if result.candidates:
            top_company_id = result.candidates[0].company.id
        
        return self._create_task(
            subject=subject,
            body=body,
            org=org,
            company_id=top_company_id,
        )
    
    def _create_review_task(self, result: MatchResult) -> TaskResult:
        """Create task for medium-confidence match needing review."""
        org = result.organization
        company = result.matched_company
        
        tag = self._make_org_tag(org.id)
        subject = f"{tag} Sync Review: Verify {org.name} → {company.name if company else 'Unknown'}"
        body = self._format_review_body(result)
        
        return self._create_task(
            subject=subject,
            body=body,
            org=org,
            company_id=company.id if company else None,
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
        subject = f"{tag} Sync: No HubSpot company found for {org.name}"
        body = self._format_no_match_body(result)
        
        return self._create_task(
            subject=subject,
            body=body,
            org=org,
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
    ) -> TaskResult:
        """Create the actual HubSpot task."""
        # Check for existing open task to avoid duplicates
        existing_task = self._check_for_existing_task(org)
        if existing_task:
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
        
        task = self.hubspot.create_task(
            subject=subject,
            body=body,
            associated_company_id=company_id,
        )
        
        if task:
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
            self.audit_log.log(
                SyncEventType.ERROR,
                message=f"Failed to create task for {org.name}",
                platform_org_id=org.id,
                platform_org_name=org.name,
            )
            return TaskResult(
                success=False,
                message=f"Failed to create task",
            )
    
    def _format_conflict_body(self, result: MatchResult) -> str:
        """Format task body for conflict scenario."""
        org = result.organization
        company = result.matched_company
        
        lines = [
            f"**Platform Organization:** {org.name}",
            f"**Platform ID:** {org.id}",
            f"**Users:** {len(org.users)}",
            "",
            f"**Matched HubSpot Company:** {company.name if company else 'Unknown'}",
            f"**Already linked to Platform ID:** {company.platform_org_id if company else 'N/A'}",
            "",
            "**Issue:** Domain match found a company that's already linked to a DIFFERENT platform org.",
            "A separate placeholder company has been created for this organization to avoid recurring conflicts.",
            "",
            "**This may indicate:**",
            "- Same company created two platform accounts (merge needed)",
            "- Two different companies sharing a domain",
            "- A user transferred to a new organization",
            "",
            "**User emails in this platform org:**",
        ]
        
        for email in org.user_emails[:10]:
            lines.append(f"- {email}")
        
        if len(org.user_emails) > 10:
            lines.append(f"- ... and {len(org.user_emails) - 10} more")
        
        lines.extend([
            "",
            "**Action needed:**",
            "1. Review both platform orgs",
            "2. Merge HubSpot companies if they're the same business",
            "3. Or keep separate if they're different businesses",
        ])
        
        return "\n".join(lines)
    
    def _format_multiple_matches_body(self, result: MatchResult) -> str:
        """Format task body for multiple matches scenario."""
        org = result.organization
        
        lines = [
            f"**Platform Organization:** {org.name}",
            f"**Platform ID:** {org.id}",
            f"**Users:** {len(org.users)}",
            "",
            "**Possible HubSpot Companies:**",
        ]
        
        for i, match in enumerate(result.candidates[:5], 1):
            company = match.company
            signals = ", ".join(s.signal_type.value for s in match.signals)
            lines.append(
                f"{i}. **{company.name or company.id}** (domain: {company.domain or 'N/A'}) "
                f"- Score: {match.score:.2f} - Signals: {signals}"
            )
        
        if len(result.candidates) > 5:
            lines.append(f"... and {len(result.candidates) - 5} more candidates")
        
        lines.extend([
            "",
            "**User emails in platform org:**",
        ])
        
        for email in org.user_emails[:5]:
            lines.append(f"- {email}")
        
        if len(org.user_emails) > 5:
            lines.append(f"- ... and {len(org.user_emails) - 5} more")
        
        lines.extend([
            "",
            "**Action needed:** Select the correct company to link.",
        ])
        
        return "\n".join(lines)
    
    def _format_review_body(self, result: MatchResult) -> str:
        """Format task body for review scenario."""
        org = result.organization
        company = result.matched_company
        
        lines = [
            f"**Platform Organization:** {org.name}",
            f"**Platform ID:** {org.id}",
            f"**Users:** {len(org.users)}",
            "",
            f"**Suggested HubSpot Company:** {company.name if company else 'Unknown'}",
            f"**Match Confidence:** {result.confidence:.2f}",
            "",
            "**Matching signals:**",
        ]
        
        if result.candidates:
            for signal in result.candidates[0].signals:
                lines.append(f"- {signal.signal_type.value}: {signal.source}")
        
        lines.extend([
            "",
            "**User emails in platform org:**",
        ])
        
        for email in org.user_emails[:5]:
            lines.append(f"- {email}")
        
        if len(org.user_emails) > 5:
            lines.append(f"- ... and {len(org.user_emails) - 5} more")
        
        lines.extend([
            "",
            "**Action needed:** Verify this match is correct and update the company's platform_org_id if so.",
        ])
        
        return "\n".join(lines)
    
    def _format_no_match_body(self, result: MatchResult) -> str:
        """Format task body for no match scenario."""
        org = result.organization
        
        lines = [
            f"**Platform Organization:** {org.name}",
            f"**Platform ID:** {org.id}",
            f"**Users:** {len(org.users)}",
            "",
            "**User emails:**",
        ]
        
        for email in org.user_emails[:10]:
            lines.append(f"- {email}")
        
        if len(org.user_emails) > 10:
            lines.append(f"- ... and {len(org.user_emails) - 10} more")
        
        admin_email = org.admin_email
        if admin_email:
            lines.extend([
                "",
                f"**Admin email:** {admin_email}",
            ])
        
        if org.paddle_id:
            lines.extend([
                f"**Paddle ID:** {org.paddle_id}",
            ])
        
        lines.extend([
            "",
            "**Issue:** No HubSpot company could be matched to this platform organization.",
            "",
            "**Action needed:** Create a company in HubSpot and set platform_org_id, or link to existing company.",
        ])
        
        return "\n".join(lines)
