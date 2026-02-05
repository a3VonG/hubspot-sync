"""
Tests for the audit logging system.
"""

import pytest
import os
import tempfile

from utils.audit import AuditLog, SyncEvent, SyncEventType


class TestAuditLog:
    """Tests for the AuditLog class."""
    
    def test_start_sync_run(self):
        """Should create unique sync run IDs."""
        audit = AuditLog(":memory:")
        
        run1 = audit.start_sync_run()
        assert run1 is not None
        assert len(run1) > 0
    
    def test_log_event(self):
        """Should log events correctly."""
        audit = AuditLog(":memory:")
        audit.start_sync_run()
        
        event = audit.log(
            SyncEventType.ORG_PROCESSED,
            message="Test message",
            platform_org_id="org-123",
            platform_org_name="Test Org",
        )
        
        assert event.event_type == SyncEventType.ORG_PROCESSED
        assert event.message == "Test message"
        assert event.platform_org_id == "org-123"
        assert len(audit.events) == 1
    
    def test_get_summary(self):
        """Should generate correct summary statistics."""
        audit = AuditLog(":memory:")
        audit.start_sync_run()
        
        # Log various events
        audit.log(SyncEventType.ORG_PROCESSED, platform_org_id="1")
        audit.log(SyncEventType.ORG_PROCESSED, platform_org_id="2")
        audit.log(SyncEventType.AUTO_LINKED, platform_org_id="1")
        audit.log(SyncEventType.CONTACT_CREATED, platform_org_id="1")
        audit.log(SyncEventType.CONTACT_ASSOCIATED, platform_org_id="1")
        audit.log(SyncEventType.TASK_CREATED, platform_org_id="2")
        audit.log(SyncEventType.ERROR, platform_org_id="3")
        
        summary = audit.get_summary()
        
        assert summary["orgs_processed"] == 2
        assert summary["auto_linked"] == 1
        assert summary["contacts_created"] == 1
        assert summary["contacts_associated"] == 1
        assert summary["tasks_created"] == 1
        assert summary["errors"] == 1
    
    def test_save_to_database(self):
        """Should persist events to SQLite."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            # Create and save events
            audit = AuditLog(db_path)
            run_id = audit.start_sync_run()
            audit.log(SyncEventType.ORG_PROCESSED, message="Test", platform_org_id="org-1")
            audit.save()
            
            # Verify by reading from new connection
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT COUNT(*) FROM sync_events WHERE sync_run_id = ?",
                (run_id,)
            )
            count = cursor.fetchone()[0]
            conn.close()
            
            assert count == 1
        finally:
            os.unlink(db_path)
    
    def test_get_events_by_org(self):
        """Should filter events by organization."""
        audit = AuditLog(":memory:")
        audit.start_sync_run()
        
        audit.log(SyncEventType.ORG_PROCESSED, platform_org_id="org-1")
        audit.log(SyncEventType.AUTO_LINKED, platform_org_id="org-1")
        audit.log(SyncEventType.ORG_PROCESSED, platform_org_id="org-2")
        
        org1_events = audit.get_events_by_org("org-1")
        
        assert len(org1_events) == 2
        assert all(e.platform_org_id == "org-1" for e in org1_events)
    
    def test_get_events_by_type(self):
        """Should filter events by type."""
        audit = AuditLog(":memory:")
        audit.start_sync_run()
        
        audit.log(SyncEventType.ORG_PROCESSED, platform_org_id="org-1")
        audit.log(SyncEventType.AUTO_LINKED, platform_org_id="org-1")
        audit.log(SyncEventType.AUTO_LINKED, platform_org_id="org-2")
        
        linked_events = audit.get_events_by_type(SyncEventType.AUTO_LINKED)
        
        assert len(linked_events) == 2
        assert all(e.event_type == SyncEventType.AUTO_LINKED for e in linked_events)
