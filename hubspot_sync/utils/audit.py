"""
Audit logging for sync operations.

Tracks all sync operations for debugging and reporting.
"""

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Any


class SyncEventType(str, Enum):
    """Types of sync events."""
    # Discovery events
    ORG_PROCESSED = "org_processed"
    CONTACT_FOUND = "contact_found"
    CONTACT_NOT_FOUND = "contact_not_found"
    COMPANY_FOUND = "company_found"
    COMPANY_NOT_FOUND = "company_not_found"
    
    # Matching events
    MATCH_BY_PLATFORM_ID = "match_by_platform_id"
    MATCH_BY_DOMAIN = "match_by_domain"
    MATCH_BY_CONTACT_ASSOCIATION = "match_by_contact_association"
    MATCH_BY_PADDLE = "match_by_paddle"
    NO_MATCH = "no_match"
    MULTIPLE_MATCHES = "multiple_matches"
    
    # Action events
    AUTO_LINKED = "auto_linked"
    CONTACT_CREATED = "contact_created"
    CONTACT_ASSOCIATED = "contact_associated"
    TASK_CREATED = "task_created"
    
    # Errors
    ERROR = "error"
    
    # Skipped
    SKIPPED = "skipped"


@dataclass
class SyncEvent:
    """A single sync event for logging."""
    timestamp: str
    event_type: SyncEventType
    platform_org_id: Optional[str] = None
    platform_org_name: Optional[str] = None
    hubspot_company_id: Optional[str] = None
    hubspot_company_name: Optional[str] = None
    hubspot_contact_id: Optional[str] = None
    email: Optional[str] = None
    message: str = ""
    details: dict = None
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}


class AuditLog:
    """
    SQLite-based audit log for sync operations.
    
    Provides persistent storage and querying of sync events.
    """
    
    def __init__(self, db_path: str = "sync_audit.db"):
        """
        Initialize the audit log.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()
        self.events: list[SyncEvent] = []
        self.sync_run_id: Optional[str] = None
    
    def _init_db(self):
        """Initialize the database schema."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_run_id TEXT,
                timestamp TEXT,
                event_type TEXT,
                platform_org_id TEXT,
                platform_org_name TEXT,
                hubspot_company_id TEXT,
                hubspot_company_name TEXT,
                hubspot_contact_id TEXT,
                email TEXT,
                message TEXT,
                details TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sync_run_id ON sync_events(sync_run_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_platform_org_id ON sync_events(platform_org_id)
        """)
        conn.commit()
        conn.close()
    
    def start_sync_run(self) -> str:
        """
        Start a new sync run.
        
        Returns:
            Unique identifier for this sync run
        """
        self.sync_run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.events = []
        return self.sync_run_id
    
    def log(self, event_type: SyncEventType, message: str = "", **kwargs) -> SyncEvent:
        """
        Log a sync event.
        
        Args:
            event_type: Type of event
            message: Human-readable message
            **kwargs: Additional event fields
            
        Returns:
            Created SyncEvent
        """
        event = SyncEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            message=message,
            **kwargs
        )
        self.events.append(event)
        return event
    
    def save(self):
        """Save all events from current sync run to database."""
        if not self.sync_run_id or not self.events:
            return
        
        conn = sqlite3.connect(self.db_path)
        for event in self.events:
            conn.execute("""
                INSERT INTO sync_events (
                    sync_run_id, timestamp, event_type, platform_org_id, 
                    platform_org_name, hubspot_company_id, hubspot_company_name,
                    hubspot_contact_id, email, message, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.sync_run_id,
                event.timestamp,
                event.event_type.value if isinstance(event.event_type, Enum) else event.event_type,
                event.platform_org_id,
                event.platform_org_name,
                event.hubspot_company_id,
                event.hubspot_company_name,
                event.hubspot_contact_id,
                event.email,
                event.message,
                json.dumps(event.details) if event.details else None,
            ))
        conn.commit()
        conn.close()
    
    def get_summary(self) -> dict[str, Any]:
        """
        Get a summary of the current sync run.
        
        Returns:
            Dictionary with counts by event type
        """
        summary = {
            "sync_run_id": self.sync_run_id,
            "total_events": len(self.events),
            "orgs_processed": 0,
            "auto_linked": 0,
            "contacts_created": 0,
            "contacts_associated": 0,
            "tasks_created": 0,
            "no_match": 0,
            "errors": 0,
            "skipped": 0,
        }
        
        for event in self.events:
            event_type = event.event_type
            if event_type == SyncEventType.ORG_PROCESSED:
                summary["orgs_processed"] += 1
            elif event_type == SyncEventType.AUTO_LINKED:
                summary["auto_linked"] += 1
            elif event_type == SyncEventType.CONTACT_CREATED:
                summary["contacts_created"] += 1
            elif event_type == SyncEventType.CONTACT_ASSOCIATED:
                summary["contacts_associated"] += 1
            elif event_type == SyncEventType.TASK_CREATED:
                summary["tasks_created"] += 1
            elif event_type == SyncEventType.NO_MATCH:
                summary["no_match"] += 1
            elif event_type == SyncEventType.ERROR:
                summary["errors"] += 1
            elif event_type == SyncEventType.SKIPPED:
                summary["skipped"] += 1
        
        return summary
    
    def get_events_by_org(self, platform_org_id: str) -> list[SyncEvent]:
        """Get all events for a specific organization from current run."""
        return [e for e in self.events if e.platform_org_id == platform_org_id]
    
    def get_events_by_type(self, event_type: SyncEventType) -> list[SyncEvent]:
        """Get all events of a specific type from current run."""
        return [e for e in self.events if e.event_type == event_type]
