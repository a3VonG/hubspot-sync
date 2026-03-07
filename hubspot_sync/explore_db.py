#!/usr/bin/env python3
"""
Database schema exploration script.

Run this to understand the actual database structure and test queries.
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from .config import DatabaseConfig
from .utils.database import DatabaseConnection


def explore_schema(conn):
    """Explore the database schema."""
    cursor = conn.cursor()
    
    # Get all tables
    print("=" * 60)
    print("TABLES IN DATABASE")
    print("=" * 60)
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        ORDER BY table_name
    """)
    tables = [row[0] for row in cursor.fetchall()]
    for table in tables:
        print(f"  - {table}")
    
    # For each relevant table, show columns
    relevant_tables = [
        "organizations", "users", "orders", "order_status", "services", 
        "usage_transactions", "payment_events", "feedback"
    ]
    
    for table in relevant_tables:
        if table not in tables:
            print(f"\n⚠️  Table '{table}' does not exist!")
            continue
            
        print(f"\n{'=' * 60}")
        print(f"TABLE: {table}")
        print("=" * 60)
        
        cursor.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        
        columns = cursor.fetchall()
        for col_name, data_type, nullable in columns:
            null_str = "" if nullable == "YES" else " NOT NULL"
            print(f"  {col_name}: {data_type}{null_str}")
        
        # Show row count
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"\n  Row count: {count:,}")
        
        # Show sample row
        cursor.execute(f"SELECT * FROM {table} LIMIT 1")
        sample = cursor.fetchone()
        if sample:
            print(f"  Sample row: {sample[:5]}..." if len(sample) > 5 else f"  Sample row: {sample}")


def test_analytics_queries(conn):
    """Test the analytics queries to find issues."""
    cursor = conn.cursor()
    
    print("\n" + "=" * 60)
    print("TESTING ANALYTICS QUERIES")
    print("=" * 60)
    
    # Test 1: usage_transactions columns
    print("\n1. Checking usage_transactions columns...")
    try:
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'usage_transactions'
        """)
        cols = [row[0] for row in cursor.fetchall()]
        print(f"   Columns: {cols}")
        
        # Check for date columns
        date_cols = [c for c in cols if 'date' in c.lower() or 'time' in c.lower() or 'at' in c.lower()]
        print(f"   Date-like columns: {date_cols}")
    except Exception as e:
        print(f"   ERROR: {e}")
    
    # Test 2: Try the actual query that failed
    print("\n2. Testing usage metrics query...")
    try:
        # First, find the correct date column
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'usage_transactions'
            AND (column_name LIKE '%date%' OR column_name LIKE '%time%' OR column_name LIKE '%_at')
        """)
        date_cols = [row[0] for row in cursor.fetchall()]
        print(f"   Available date columns: {date_cols}")
        
        if date_cols:
            date_col = date_cols[0]
            cursor.execute(f"""
                SELECT organization_id, 
                       MAX({date_col}) as last_usage,
                       COUNT(*) as total_transactions
                FROM usage_transactions
                GROUP BY organization_id
                LIMIT 3
            """)
            results = cursor.fetchall()
            print(f"   Sample results using '{date_col}': {results}")
    except Exception as e:
        print(f"   ERROR: {e}")
        conn.rollback()
    
    # Test 3: Check orders table
    print("\n3. Checking orders table structure...")
    try:
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'orders'
            AND (column_name LIKE '%status%' OR column_name LIKE '%state%')
        """)
        status_cols = [row[0] for row in cursor.fetchall()]
        print(f"   Status columns: {status_cols}")
        
        if status_cols:
            cursor.execute(f"SELECT DISTINCT {status_cols[0]} FROM orders LIMIT 10")
            statuses = [row[0] for row in cursor.fetchall()]
            print(f"   Distinct values: {statuses}")
    except Exception as e:
        print(f"   ERROR: {e}")
        conn.rollback()


def main():
    print("Connecting to database...")
    db_config = DatabaseConfig.from_env()
    
    db = DatabaseConnection(db_config)
    try:
        conn = db.connect()
        print("✅ Connected!\n")
        
        explore_schema(conn)
        test_analytics_queries(conn)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
