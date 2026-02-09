#!/usr/bin/env python3
"""
Debug script for analytics queries.
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import DatabaseConfig
from utils.database import DatabaseConnection
from datetime import datetime, timedelta, timezone

ORG_ID = "7d12f6d6-4f6b-4c62-b9a9-a384fd27e025"  # Org with failed cases and refunds


def debug_analytics(conn):
    cursor = conn.cursor()
    now = datetime.now(timezone.utc)
    days_30_ago = now - timedelta(days=30)
    
    print(f"Debug for org: {ORG_ID}")
    print(f"Date range: {days_30_ago} to {now}")
    print("=" * 60)
    
    # 1. JOBS TABLE STRUCTURE
    print("\n1. JOBS TABLE STRUCTURE:")
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'jobs'
        ORDER BY ordinal_position
    """)
    for row in cursor.fetchall():
        print(f"   {row[0]}: {row[1]}")
    
    # 2. Check distinct job status values
    print("\n2. DISTINCT JOB STATUS VALUES:")
    cursor.execute("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'jobs' 
        AND (column_name LIKE '%status%' OR column_name LIKE '%state%')
    """)
    status_cols = [row[0] for row in cursor.fetchall()]
    print(f"   Status columns in jobs: {status_cols}")
    
    for col in status_cols:
        cursor.execute(f"SELECT DISTINCT {col} FROM jobs LIMIT 20")
        values = [row[0] for row in cursor.fetchall()]
        print(f"   Distinct {col} values: {values}")
    
    # 3. Relationship between orders and jobs
    print("\n3. ORDERS vs JOBS RELATIONSHIP:")
    cursor.execute("""
        SELECT COUNT(*) FROM orders o
        JOIN users u ON o.user_id = u.id
        WHERE u.organization_id = %s
    """, (ORG_ID,))
    order_count = cursor.fetchone()[0]
    print(f"   Total orders for org: {order_count}")
    
    cursor.execute("""
        SELECT COUNT(*) FROM jobs j
        JOIN orders o ON j.order_id = o.id
        JOIN users u ON o.user_id = u.id
        WHERE u.organization_id = %s
    """, (ORG_ID,))
    job_count = cursor.fetchone()[0]
    print(f"   Total jobs for org: {job_count}")
    
    # 4. Job status breakdown for this org
    print("\n4. JOB STATUS BREAKDOWN (all time):")
    for col in status_cols:
        cursor.execute(f"""
            SELECT j.{col}, COUNT(*) as cnt
            FROM jobs j
            JOIN orders o ON j.order_id = o.id
            JOIN users u ON o.user_id = u.id
            WHERE u.organization_id = %s
            GROUP BY j.{col}
            ORDER BY cnt DESC
        """, (ORG_ID,))
        print(f"   By {col}:")
        for row in cursor.fetchall():
            print(f"      {row[0]}: {row[1]}")
    
    # 5. Job status in last 30 days
    print("\n5. JOB STATUS BREAKDOWN (last 30 days):")
    for col in status_cols:
        cursor.execute(f"""
            SELECT j.{col}, COUNT(*) as cnt
            FROM jobs j
            JOIN orders o ON j.order_id = o.id
            JOIN users u ON o.user_id = u.id
            WHERE u.organization_id = %s
            AND o.timestamp >= %s
            GROUP BY j.{col}
            ORDER BY cnt DESC
        """, (ORG_ID, days_30_ago))
        print(f"   By {col}:")
        for row in cursor.fetchall():
            print(f"      {row[0]}: {row[1]}")
    
    # 6. FEEDBACK/REFUNDS
    print("\n6. FEEDBACK TABLE STRUCTURE:")
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'feedback'
        ORDER BY ordinal_position
    """)
    for row in cursor.fetchall():
        print(f"   {row[0]}: {row[1]}")
    
    print("\n7. FEEDBACK/REFUNDS FOR THIS ORG:")
    cursor.execute("""
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN f.request_refund = true THEN 1 END) as refund_requests,
               COUNT(CASE WHEN f.awarded_amount > 0 THEN 1 END) as awarded_refunds
        FROM feedback f
        JOIN orders o ON f.order_id = o.id
        JOIN users u ON o.user_id = u.id
        WHERE u.organization_id = %s
    """, (ORG_ID,))
    row = cursor.fetchone()
    print(f"   Total feedback entries: {row[0]}")
    print(f"   Refund requests: {row[1]}")
    print(f"   Awarded refunds: {row[2]}")
    
    print("\n8. FEEDBACK IN LAST 30 DAYS:")
    cursor.execute("""
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN f.request_refund = true THEN 1 END) as refund_requests,
               SUM(COALESCE(f.awarded_amount, 0)) as total_awarded
        FROM feedback f
        JOIN orders o ON f.order_id = o.id
        JOIN users u ON o.user_id = u.id
        WHERE u.organization_id = %s
        AND f.created_at >= %s
    """, (ORG_ID, days_30_ago))
    row = cursor.fetchone()
    print(f"   Total feedback: {row[0]}")
    print(f"   Refund requests: {row[1]}")
    print(f"   Total awarded amount: {row[2]}")
    
    # 9. Sample feedback entries
    print("\n9. SAMPLE FEEDBACK ENTRIES:")
    cursor.execute("""
        SELECT f.feedback_text, f.request_refund, f.awarded_amount, f.created_at
        FROM feedback f
        JOIN orders o ON f.order_id = o.id
        JOIN users u ON o.user_id = u.id
        WHERE u.organization_id = %s
        ORDER BY f.created_at DESC
        LIMIT 5
    """, (ORG_ID,))
    for row in cursor.fetchall():
        print(f"   - '{row[0][:50]}...' refund={row[1]} awarded={row[2]} date={row[3]}")
    
    # 10. Order status (manual review) breakdown
    print("\n10. ORDER STATUS (manual review) BREAKDOWN:")
    cursor.execute("""
        SELECT os.status, COUNT(*) as cnt
        FROM orders o
        JOIN users u ON o.user_id = u.id
        LEFT JOIN order_status os ON os.order_id = o.id
        WHERE u.organization_id = %s
        GROUP BY os.status
        ORDER BY cnt DESC
    """, (ORG_ID,))
    for row in cursor.fetchall():
        print(f"   {row[0]}: {row[1]}")
    
    # 11. Usage transactions breakdown
    print("\n11. USAGE TRANSACTIONS BREAKDOWN:")
    cursor.execute("""
        SELECT type, COUNT(*) as cnt, SUM(amount) as total_amount
        FROM usage_transactions
        WHERE organization_id = %s
        GROUP BY type
        ORDER BY cnt DESC
    """, (ORG_ID,))
    for row in cursor.fetchall():
        print(f"   {row[0]}: {row[1]} transactions, total amount: {row[2]:.2f}")


def main():
    print("Connecting to database...")
    db_config = DatabaseConfig.from_env()
    
    db = DatabaseConnection(db_config)
    try:
        conn = db.connect()
        print("✅ Connected!\n")
        debug_analytics(conn)
    except Exception as e:
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
