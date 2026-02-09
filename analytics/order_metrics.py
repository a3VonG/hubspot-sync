"""
Order metrics computation from orders and related tables.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from psycopg2.extras import RealDictCursor


@dataclass
class OrderMetrics:
    """Order-related metrics for an organization."""
    # Success/failure counts (all time for testing)
    successful_cases: int = 0
    failed_cases: int = 0
    
    # Error count in last 30 days
    errors_last_30_days: int = 0
    
    # Services used - dict maps service name to count
    services_used_last_30_days: dict[str, int] = field(default_factory=dict)
    services_used_all_time: dict[str, int] = field(default_factory=dict)
    
    # Refunds/feedback
    refunds_and_feedback_last_30_days: int = 0
    
    def format_services_with_counts(self, services: dict[str, int]) -> str:
        """Format services dict as 'Service1 (10), Service2 (5)'."""
        if not services:
            return ""
        return ", ".join(f"{name} ({count})" for name, count in sorted(services.items(), key=lambda x: -x[1]))


class OrderMetricsComputer:
    """Computes order metrics from orders and related tables."""
    
    # Job status values (from jobs table - processing outcome)
    JOB_STATUS_DONE = "Done"
    JOB_STATUS_FAILED = "Failed"
    JOB_STATUS_SUBMITTED = "Submitted"
    JOB_STATUS_CANCELLED = "Cancelled"
    
    def __init__(self, db_connection):
        """
        Initialize with database connection.
        
        Args:
            db_connection: psycopg2 connection
        """
        self.conn = db_connection
    
    def compute_for_organization(self, org_id: str) -> OrderMetrics:
        """
        Compute order metrics for a single organization.
        
        Args:
            org_id: Organization UUID
            
        Returns:
            OrderMetrics with computed values
        """
        metrics = OrderMetrics()
        now = datetime.now(timezone.utc)
        days_30_ago = now - timedelta(days=30)
        
        # Get order counts and services
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get job status counts - jobs table tracks processing outcome
            # Done = successful, Failed = error
            cur.execute("""
                SELECT
                    -- Successful = Done jobs
                    COUNT(CASE WHEN j.job_status = %(status_done)s THEN 1 END) as successful_cases,
                    
                    -- Failed jobs
                    COUNT(CASE WHEN j.job_status = %(status_failed)s THEN 1 END) as failed_cases,
                    
                    -- Errors in last 30 days (Failed jobs)
                    COUNT(CASE 
                        WHEN j.job_status = %(status_failed)s 
                        AND o.timestamp >= %(days_30_ago)s 
                        THEN 1 
                    END) as errors_30_days
                    
                FROM orders o
                JOIN users u ON o.user_id = u.id
                LEFT JOIN jobs j ON j.order_id = o.id
                WHERE u.organization_id = %(org_id)s
            """, {
                "org_id": org_id,
                "status_done": self.JOB_STATUS_DONE,
                "status_failed": self.JOB_STATUS_FAILED,
                "days_30_ago": days_30_ago,
            })
            
            row = cur.fetchone()
            if row:
                metrics.successful_cases = row["successful_cases"] or 0
                metrics.failed_cases = row["failed_cases"] or 0
                metrics.errors_last_30_days = row["errors_30_days"] or 0
        
        # Get services used with counts
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    s.name as service_name,
                    COUNT(*) as total_count,
                    COUNT(CASE WHEN o.timestamp >= %(days_30_ago)s THEN 1 END) as recent_count
                FROM orders o
                JOIN users u ON o.user_id = u.id
                JOIN services s ON o.service_id = s.id
                WHERE u.organization_id = %(org_id)s
                AND s.name IS NOT NULL
                GROUP BY s.name
            """, {
                "org_id": org_id,
                "days_30_ago": days_30_ago,
            })
            
            all_services = {}
            recent_services = {}
            
            for row in cur.fetchall():
                service_name = row["service_name"]
                all_services[service_name] = row["total_count"]
                if row["recent_count"] > 0:
                    recent_services[service_name] = row["recent_count"]
            
            metrics.services_used_all_time = all_services
            metrics.services_used_last_30_days = recent_services
        
        # Get refunds/feedback count
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) as feedback_count
                FROM feedback f
                JOIN orders o ON f.order_id = o.id
                JOIN users u ON o.user_id = u.id
                WHERE u.organization_id = %(org_id)s
                AND f.created_at >= %(days_30_ago)s
            """, {
                "org_id": org_id,
                "days_30_ago": days_30_ago,
            })
            
            row = cur.fetchone()
            if row:
                metrics.refunds_and_feedback_last_30_days = row["feedback_count"] or 0
        
        return metrics
    
    def compute_for_organizations_batch(self, org_ids: list[str]) -> dict[str, OrderMetrics]:
        """
        Compute order metrics for multiple organizations efficiently.
        
        Args:
            org_ids: List of organization UUIDs
            
        Returns:
            Dictionary mapping org_id to OrderMetrics
        """
        if not org_ids:
            return {}
        
        results = {org_id: OrderMetrics() for org_id in org_ids}
        now = datetime.now(timezone.utc)
        days_30_ago = now - timedelta(days=30)
        
        # Get job status counts - jobs table tracks processing outcome
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    u.organization_id,
                    COUNT(CASE WHEN j.job_status = %(status_done)s THEN 1 END) as successful_cases,
                    COUNT(CASE WHEN j.job_status = %(status_failed)s THEN 1 END) as failed_cases,
                    COUNT(CASE 
                        WHEN j.job_status = %(status_failed)s 
                        AND o.timestamp >= %(days_30_ago)s 
                        THEN 1 
                    END) as errors_30_days
                    
                FROM orders o
                JOIN users u ON o.user_id = u.id
                LEFT JOIN jobs j ON j.order_id = o.id
                WHERE u.organization_id = ANY(%(org_ids)s)
                GROUP BY u.organization_id
            """, {
                "org_ids": org_ids,
                "status_done": self.JOB_STATUS_DONE,
                "status_failed": self.JOB_STATUS_FAILED,
                "days_30_ago": days_30_ago,
            })
            
            for row in cur.fetchall():
                org_id = str(row["organization_id"])
                if org_id in results:
                    results[org_id].successful_cases = row["successful_cases"] or 0
                    results[org_id].failed_cases = row["failed_cases"] or 0
                    results[org_id].errors_last_30_days = row["errors_30_days"] or 0
        
        # Get services per org with counts
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    u.organization_id,
                    s.name as service_name,
                    COUNT(*) as total_count,
                    COUNT(CASE WHEN o.timestamp >= %(days_30_ago)s THEN 1 END) as recent_count
                FROM orders o
                JOIN users u ON o.user_id = u.id
                JOIN services s ON o.service_id = s.id
                WHERE u.organization_id = ANY(%(org_ids)s)
                AND s.name IS NOT NULL
                GROUP BY u.organization_id, s.name
            """, {
                "org_ids": org_ids,
                "days_30_ago": days_30_ago,
            })
            
            # Build service dicts per org
            for row in cur.fetchall():
                org_id = str(row["organization_id"])
                if org_id in results:
                    service_name = row["service_name"]
                    results[org_id].services_used_all_time[service_name] = row["total_count"]
                    if row["recent_count"] > 0:
                        results[org_id].services_used_last_30_days[service_name] = row["recent_count"]
        
        # Get feedback counts
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    u.organization_id,
                    COUNT(*) as feedback_count
                FROM feedback f
                JOIN orders o ON f.order_id = o.id
                JOIN users u ON o.user_id = u.id
                WHERE u.organization_id = ANY(%(org_ids)s)
                AND f.created_at >= %(days_30_ago)s
                GROUP BY u.organization_id
            """, {
                "org_ids": org_ids,
                "days_30_ago": days_30_ago,
            })
            
            for row in cur.fetchall():
                org_id = str(row["organization_id"])
                if org_id in results:
                    results[org_id].refunds_and_feedback_last_30_days = row["feedback_count"] or 0
        
        return results
