"""
Usage metrics computation from usage_transactions table.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from psycopg2.extras import RealDictCursor


@dataclass
class UsageMetrics:
    """Usage metrics for an organization."""
    last_usage_date: Optional[datetime] = None
    usage_last_7_days: float = 0.0
    usage_last_30_days: float = 0.0
    usage_previous_30_days: float = 0.0  # For trend calculation
    usage_trend: str = "stable"
    signed_up_date: Optional[datetime] = None  # First GIFT_TOPUP transaction
    has_used_product: bool = False  # Has at least one ORDER_USAGE transaction (all-time)


class UsageMetricsComputer:
    """Computes usage metrics from usage_transactions table."""
    
    # Transaction type for counting usage
    ORDER_USAGE_TYPE = "ORDER_USAGE"
    GIFT_TOPUP_TYPE = "GIFT_TOPUP"
    
    def __init__(self, db_connection):
        """
        Initialize with database connection.
        
        Args:
            db_connection: psycopg2 connection
        """
        self.conn = db_connection
    
    def compute_for_organization(self, org_id: str) -> UsageMetrics:
        """
        Compute usage metrics for a single organization.
        
        Args:
            org_id: Organization UUID
            
        Returns:
            UsageMetrics with computed values
        """
        metrics = UsageMetrics()
        now = datetime.now(timezone.utc)
        
        # Compute all metrics in a single query for efficiency
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    -- Last usage date (any transaction)
                    MAX(date) as last_usage_date,
                    
                    -- Usage counts (ORDER_USAGE only) - use ABS(amount)
                    COALESCE(SUM(CASE 
                        WHEN type = %(order_usage)s 
                        AND date >= %(days_7_ago)s 
                        THEN ABS(amount) ELSE 0 
                    END), 0) as usage_7_days,
                    
                    COALESCE(SUM(CASE 
                        WHEN type = %(order_usage)s 
                        AND date >= %(days_30_ago)s 
                        THEN ABS(amount) ELSE 0 
                    END), 0) as usage_30_days,
                    
                    COALESCE(SUM(CASE 
                        WHEN type = %(order_usage)s 
                        AND date >= %(days_60_ago)s 
                        AND date < %(days_30_ago)s 
                        THEN ABS(amount) ELSE 0 
                    END), 0) as usage_prev_30_days,
                    
                    -- Signup date (first GIFT_TOPUP)
                    MIN(CASE 
                        WHEN type = %(gift_topup)s 
                        THEN date 
                    END) as signed_up_date,
                    
                    -- Has at least one real ORDER_USAGE transaction
                    BOOL_OR(type = %(order_usage)s) as has_used_product
                    
                FROM usage_transactions
                WHERE organization_id = %(org_id)s
            """, {
                "org_id": org_id,
                "order_usage": self.ORDER_USAGE_TYPE,
                "gift_topup": self.GIFT_TOPUP_TYPE,
                "days_7_ago": now - timedelta(days=7),
                "days_30_ago": now - timedelta(days=30),
                "days_60_ago": now - timedelta(days=60),
            })
            
            row = cur.fetchone()
            
            if row:
                metrics.last_usage_date = row["last_usage_date"]
                metrics.usage_last_7_days = float(row["usage_7_days"] or 0)
                metrics.usage_last_30_days = float(row["usage_30_days"] or 0)
                metrics.usage_previous_30_days = float(row["usage_prev_30_days"] or 0)
                metrics.signed_up_date = row["signed_up_date"]
                metrics.has_used_product = bool(row.get("has_used_product"))
                
                # Calculate trend
                metrics.usage_trend = self._calculate_trend(
                    metrics.usage_last_30_days,
                    metrics.usage_previous_30_days,
                )
        
        return metrics
    
    def compute_for_organizations_batch(self, org_ids: list[str]) -> dict[str, UsageMetrics]:
        """
        Compute usage metrics for multiple organizations efficiently.
        
        Args:
            org_ids: List of organization UUIDs
            
        Returns:
            Dictionary mapping org_id to UsageMetrics
        """
        if not org_ids:
            return {}
        
        results = {org_id: UsageMetrics() for org_id in org_ids}
        now = datetime.now(timezone.utc)
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    organization_id,
                    MAX(date) as last_usage_date,
                    
                    COALESCE(SUM(CASE 
                        WHEN type = %(order_usage)s 
                        AND date >= %(days_7_ago)s 
                        THEN ABS(amount) ELSE 0 
                    END), 0) as usage_7_days,
                    
                    COALESCE(SUM(CASE 
                        WHEN type = %(order_usage)s 
                        AND date >= %(days_30_ago)s 
                        THEN ABS(amount) ELSE 0 
                    END), 0) as usage_30_days,
                    
                    COALESCE(SUM(CASE 
                        WHEN type = %(order_usage)s 
                        AND date >= %(days_60_ago)s 
                        AND date < %(days_30_ago)s 
                        THEN ABS(amount) ELSE 0 
                    END), 0) as usage_prev_30_days,
                    
                    MIN(CASE 
                        WHEN type = %(gift_topup)s 
                        THEN date 
                    END) as signed_up_date,
                    
                    BOOL_OR(type = %(order_usage)s) as has_used_product
                    
                FROM usage_transactions
                WHERE organization_id = ANY(%(org_ids)s)
                GROUP BY organization_id
            """, {
                "org_ids": org_ids,
                "order_usage": self.ORDER_USAGE_TYPE,
                "gift_topup": self.GIFT_TOPUP_TYPE,
                "days_7_ago": now - timedelta(days=7),
                "days_30_ago": now - timedelta(days=30),
                "days_60_ago": now - timedelta(days=60),
            })
            
            for row in cur.fetchall():
                org_id = str(row["organization_id"])
                metrics = results.get(org_id, UsageMetrics())
                
                metrics.last_usage_date = row["last_usage_date"]
                metrics.usage_last_7_days = float(row["usage_7_days"] or 0)
                metrics.usage_last_30_days = float(row["usage_30_days"] or 0)
                metrics.usage_previous_30_days = float(row["usage_prev_30_days"] or 0)
                metrics.signed_up_date = row["signed_up_date"]
                metrics.has_used_product = bool(row.get("has_used_product"))
                metrics.usage_trend = self._calculate_trend(
                    metrics.usage_last_30_days,
                    metrics.usage_previous_30_days,
                )
                
                results[org_id] = metrics
        
        return results
    
    def _calculate_trend(self, current: float, previous: float) -> str:
        """
        Calculate usage trend based on current vs previous period.
        
        Returns "up", "down", or "stable".
        """
        if previous == 0 and current == 0:
            return "stable"
        if previous == 0:
            return "up" if current > 0 else "stable"
        
        change_percent = ((current - previous) / previous) * 100
        
        if change_percent > 10:
            return "up"
        elif change_percent < -10:
            return "down"
        else:
            return "stable"
