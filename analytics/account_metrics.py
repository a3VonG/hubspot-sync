"""
Account metrics computation from organizations and users tables.
"""

from dataclasses import dataclass
from typing import Optional

from psycopg2.extras import RealDictCursor


@dataclass
class AccountMetrics:
    """Account-related metrics for an organization."""
    admin_email: Optional[str] = None
    user_count: int = 0
    free_credits_remaining: float = 0.0
    scopes: list[str] = None  # Organization scopes
    
    def __post_init__(self):
        if self.scopes is None:
            self.scopes = []
    
    @property
    def has_no_billing_scope(self) -> bool:
        """Check if organization has NO_BILLING scope."""
        return "NO_BILLING" in self.scopes


class AccountMetricsComputer:
    """Computes account metrics from organizations and users tables."""
    
    def __init__(self, db_connection):
        """
        Initialize with database connection.
        
        Args:
            db_connection: psycopg2 connection
        """
        self.conn = db_connection
    
    def compute_for_organization(self, org_id: str) -> AccountMetrics:
        """
        Compute account metrics for a single organization.
        
        Args:
            org_id: Organization UUID
            
        Returns:
            AccountMetrics with computed values
        """
        metrics = AccountMetrics()
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get organization data
            cur.execute("""
                SELECT 
                    o.usage,
                    o.scopes,
                    o.admin_user_id,
                    admin_user.email as admin_email,
                    (SELECT COUNT(*) FROM users WHERE organization_id = o.id) as user_count
                FROM organizations o
                LEFT JOIN users admin_user ON o.admin_user_id = admin_user.id
                WHERE o.id = %(org_id)s
            """, {"org_id": org_id})
            
            row = cur.fetchone()
            if row:
                metrics.admin_email = row["admin_email"]
                metrics.user_count = row["user_count"] or 0
                # Note: organizations.usage represents credits remaining (can be negative if overdrawn)
                metrics.free_credits_remaining = -float(row["usage"] or 0)
                
                # Parse scopes (stored as varchar array)
                scopes = row["scopes"]
                if scopes:
                    if isinstance(scopes, list):
                        metrics.scopes = scopes
                    elif isinstance(scopes, str):
                        # Handle if stored as comma-separated string
                        metrics.scopes = [s.strip() for s in scopes.split(",") if s.strip()]
        
        return metrics
    
    def compute_for_organizations_batch(self, org_ids: list[str]) -> dict[str, AccountMetrics]:
        """
        Compute account metrics for multiple organizations efficiently.
        
        Args:
            org_ids: List of organization UUIDs
            
        Returns:
            Dictionary mapping org_id to AccountMetrics
        """
        if not org_ids:
            return {}
        
        results = {org_id: AccountMetrics() for org_id in org_ids}
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    o.id as organization_id,
                    o.usage,
                    o.scopes,
                    admin_user.email as admin_email,
                    (SELECT COUNT(*) FROM users WHERE organization_id = o.id) as user_count
                FROM organizations o
                LEFT JOIN users admin_user ON o.admin_user_id = admin_user.id
                WHERE o.id = ANY(%(org_ids)s)
            """, {"org_ids": org_ids})
            
            for row in cur.fetchall():
                org_id = str(row["organization_id"])
                if org_id in results:
                    metrics = results[org_id]
                    metrics.admin_email = row["admin_email"]
                    metrics.user_count = row["user_count"] or 0
                    # Note: organizations.usage represents credits remaining (can be negative if overdrawn)
                    metrics.free_credits_remaining = -float(row["usage"] or 0)
                    
                    scopes = row["scopes"]
                    if scopes:
                        if isinstance(scopes, list):
                            metrics.scopes = scopes
                        elif isinstance(scopes, str):
                            metrics.scopes = [s.strip() for s in scopes.split(",") if s.strip()]
        
        return results
