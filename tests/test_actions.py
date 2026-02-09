"""
Tests for sync actions (linker, contact_sync, task_creator).
"""

import pytest
from unittest.mock import MagicMock

from actions.linker import Linker, LinkResult
from actions.contact_sync import ContactSyncer, ContactSyncResult
from actions.task_creator import TaskCreator, TaskResult
from matching.matcher import MatchResult, MatchType
from clients.platform import Organization, User
from clients.hubspot import Company, Contact
from utils.audit import AuditLog, SyncEventType


class TestLinker:
    """Tests for the Linker class."""
    
    def test_link_success(self, config, sample_organization, sample_company):
        """Should successfully link organization to company."""
        hubspot = MagicMock()
        hubspot.update_company_platform_org_id.return_value = True
        
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        # Mock get_company_by_id for standard_lab check
        hubspot.get_company_by_id.return_value = sample_company
        hubspot.update_company.return_value = True
        
        linker = Linker(hubspot, config, audit_log)
        result = linker.link_organization_to_company(sample_organization, sample_company)
        
        assert result.success is True
        assert result.was_already_linked is False
        hubspot.update_company.assert_called_once()
    
    def test_link_already_linked(self, config, sample_organization, sample_company_with_platform_id):
        """Should recognize already linked companies."""
        hubspot = MagicMock()
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        linker = Linker(hubspot, config, audit_log)
        result = linker.link_organization_to_company(
            sample_organization, sample_company_with_platform_id
        )
        
        assert result.success is True
        assert result.was_already_linked is True
        hubspot.update_company_platform_org_id.assert_not_called()
    
    def test_link_conflict(self, config, sample_organization):
        """Should fail when company has different platform ID."""
        conflicting_company = Company(
            id="conflict",
            name="Conflict",
            domain="conflict.com",
            platform_org_id="different-org",
        )
        
        hubspot = MagicMock()
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        linker = Linker(hubspot, config, audit_log)
        result = linker.link_organization_to_company(sample_organization, conflicting_company)
        
        assert result.success is False
        assert "Conflict" in result.message
    
    def test_link_dry_run(self, config, sample_organization, sample_company):
        """Should not make changes in dry run mode."""
        hubspot = MagicMock()
        config.dry_run = True
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        linker = Linker(hubspot, config, audit_log)
        result = linker.link_organization_to_company(sample_organization, sample_company)
        
        assert result.success is True
        assert "[DRY RUN]" in result.message
        hubspot.update_company_platform_org_id.assert_not_called()


class TestContactSyncer:
    """Tests for the ContactSyncer class."""
    
    def test_sync_creates_contact(self, config, sample_organization, sample_company):
        """Should create contact when it doesn't exist."""
        hubspot = MagicMock()
        hubspot.get_contact_by_email.return_value = None
        hubspot.create_contact.return_value = Contact(
            id="new-contact",
            email="admin@acme.com",
            associated_company_ids=[],
        )
        hubspot.associate_contact_with_company.return_value = True
        
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        syncer = ContactSyncer(hubspot, config, audit_log)
        result = syncer.sync_organization_contacts(sample_organization, sample_company)
        
        assert len(result.contacts_created) > 0
        assert result.success is True
    
    def test_sync_associates_existing_contact(self, config, sample_organization, sample_company):
        """Should associate existing contact with company."""
        existing_contact = Contact(
            id="existing",
            email="admin@acme.com",
            associated_company_ids=[],  # Not associated yet
        )
        
        hubspot = MagicMock()
        hubspot.get_contact_by_email.return_value = existing_contact
        hubspot.associate_contact_with_company.return_value = True
        
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        syncer = ContactSyncer(hubspot, config, audit_log)
        result = syncer.sync_organization_contacts(sample_organization, sample_company)
        
        assert len(result.contacts_associated) > 0
        hubspot.associate_contact_with_company.assert_called()
    
    def test_sync_skips_already_associated(self, config, sample_organization, sample_company):
        """Should skip contacts already associated with company."""
        associated_contact = Contact(
            id="existing",
            email="admin@acme.com",
            associated_company_ids=[sample_company.id],  # Already associated
        )
        
        hubspot = MagicMock()
        hubspot.get_contact_by_email.return_value = associated_contact
        
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        syncer = ContactSyncer(hubspot, config, audit_log)
        result = syncer.sync_organization_contacts(sample_organization, sample_company)
        
        assert len(result.contacts_already_associated) > 0
        hubspot.associate_contact_with_company.assert_not_called()


class TestTaskCreator:
    """Tests for the TaskCreator class."""
    
    def test_create_conflict_task(self, config, sample_organization, sample_company):
        """Should create task for conflict scenario."""
        hubspot = MagicMock()
        hubspot.search_tasks_by_subject.return_value = []
        hubspot.create_task.return_value = MagicMock(id="task-1", subject="Test")
        
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        match_result = MatchResult(
            match_type=MatchType.CONFLICT,
            organization=sample_organization,
            matched_company=sample_company,
            message="Conflict",
        )
        
        creator = TaskCreator(hubspot, config, audit_log)
        result = creator.create_task_for_match_result(match_result)
        
        assert result.success is True
        assert result.task is not None
        hubspot.create_task.assert_called_once()
        call_args = hubspot.create_task.call_args
        # Should include org tag and "Conflict"
        assert f"[ORG:{sample_organization.id}]" in call_args.kwargs["subject"]
        assert "Conflict" in call_args.kwargs["subject"]
    
    def test_skip_duplicate_task(self, config, sample_organization, sample_company):
        """Should skip creating duplicate tasks for open tasks."""
        # Existing open task with org tag
        existing_task = MagicMock(
            id="existing",
            subject=f"[ORG:{sample_organization.id}] Sync Conflict: Test",
            body=f"Platform ID: {sample_organization.id}",
            status="NOT_STARTED",
        )
        
        hubspot = MagicMock()
        hubspot.search_tasks_by_subject.return_value = [existing_task]
        
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        match_result = MatchResult(
            match_type=MatchType.CONFLICT,
            organization=sample_organization,
            matched_company=sample_company,
            message="Conflict",
        )
        
        creator = TaskCreator(hubspot, config, audit_log)
        result = creator.create_task_for_match_result(match_result)
        
        assert result.success is True
        assert result.skipped is True
        hubspot.create_task.assert_not_called()
    
    def test_create_task_if_previous_completed(self, config, sample_organization, sample_company):
        """Should create new task if previous task was completed."""
        # Existing but completed task
        completed_task = MagicMock(
            id="completed",
            subject=f"[ORG:{sample_organization.id}] Sync Conflict: Test",
            body=f"Platform ID: {sample_organization.id}",
            status="COMPLETED",
        )
        
        hubspot = MagicMock()
        hubspot.search_tasks_by_subject.return_value = [completed_task]
        hubspot.create_task.return_value = MagicMock(id="task-2", subject="New Task")
        
        config.dry_run = False
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        match_result = MatchResult(
            match_type=MatchType.CONFLICT,
            organization=sample_organization,
            matched_company=sample_company,
            message="Conflict",
        )
        
        creator = TaskCreator(hubspot, config, audit_log)
        result = creator.create_task_for_match_result(match_result)
        
        # Should create new task since previous was completed
        assert result.success is True
        assert result.skipped is False
        hubspot.create_task.assert_called_once()
    
    def test_no_task_for_auto_link(self, config, sample_organization, sample_company):
        """Should not create task for auto-link scenarios."""
        hubspot = MagicMock()
        audit_log = AuditLog(":memory:")
        audit_log.start_sync_run()
        
        match_result = MatchResult(
            match_type=MatchType.AUTO_LINK,
            organization=sample_organization,
            matched_company=sample_company,
            message="Auto-linked",
        )
        
        creator = TaskCreator(hubspot, config, audit_log)
        result = creator.create_task_for_match_result(match_result)
        
        assert result.success is True
        assert result.skipped is True
        hubspot.create_task.assert_not_called()
