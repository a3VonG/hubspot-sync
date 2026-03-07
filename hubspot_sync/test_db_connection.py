#!/usr/bin/env python3
"""
Test script for database connection.

Tests the database connection with SSH tunnel support.

Usage:
    python test_db_connection.py
"""

import os
import sys
from datetime import datetime

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Note: python-dotenv not installed, using system environment variables")

from .config import DatabaseConfig
from .utils.database import DatabaseConnection


def test_connection():
    """Test the database connection."""
    print("=" * 60)
    print("Database Connection Test")
    print("=" * 60)
    print()
    
    # Load configuration
    try:
        db_config = DatabaseConfig.from_env()
    except KeyError as e:
        print(f"❌ Missing environment variable: {e}")
        print("\nRequired variables:")
        print("  - DB_HOST")
        print("  - DB_NAME")
        print("  - DB_USER")
        print("  - DB_PASSWORD")
        print("  - SSH_HOST (if using tunnel)")
        print("  - SSH_USER (if using tunnel)")
        print("  - SSH_KEY_PATH or SSH_KEY_BASE64 (if using tunnel)")
        sys.exit(1)
    
    # Print configuration (mask sensitive data)
    print("Configuration:")
    print(f"  Database Host:    {db_config.host}")
    print(f"  Database Port:    {db_config.port}")
    print(f"  Database Name:    {db_config.name}")
    print(f"  Database User:    {db_config.user}")
    print(f"  Database Password: {'*' * len(db_config.password)}")
    print()
    
    if db_config.requires_tunnel:
        print("SSH Tunnel:")
        print(f"  SSH Host:         {db_config.ssh_host}")
        print(f"  SSH User:         {db_config.ssh_user}")
        if db_config.ssh_key_base64:
            if db_config.ssh_key_base64.startswith("op://"):
                print(f"  SSH Key:          {db_config.ssh_key_base64} (1Password)")
            else:
                print(f"  SSH Key:          (base64 encoded)")
        elif db_config.ssh_key_path:
            if db_config.ssh_key_path.startswith("op://"):
                print(f"  SSH Key:          {db_config.ssh_key_path} (1Password)")
            else:
                print(f"  SSH Key Path:     {db_config.ssh_key_path}")
        print()
    else:
        print("SSH Tunnel: Not configured (direct connection)")
        print()
    
    # Test connection
    print("Testing connection...")
    print("-" * 40)
    
    db = DatabaseConnection(db_config)
    
    try:
        # Connect
        if db_config.requires_tunnel:
            print("1. Starting SSH tunnel...", end=" ", flush=True)
        else:
            print("1. Connecting to database...", end=" ", flush=True)
        
        conn = db.connect()
        print("✓")
        
        # Test query
        print("2. Running test query...", end=" ", flush=True)
        with conn.cursor() as cur:
            # Test basic connectivity
            cur.execute("SELECT version();")
            version = cur.fetchone()[0]
            print("✓")
            
            # Get some stats
            print("3. Fetching database info...", end=" ", flush=True)
            cur.execute("""
                SELECT 
                    current_database() as db_name,
                    current_user as db_user,
                    inet_server_addr() as server_addr,
                    inet_server_port() as server_port
            """)
            info = cur.fetchone()
            print("✓")
            
            # Check for organizations table
            print("4. Checking organizations table...", end=" ", flush=True)
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.tables 
                WHERE table_name = 'organizations'
            """)
            has_orgs_table = cur.fetchone()[0] > 0
            
            if has_orgs_table:
                cur.execute("SELECT COUNT(*) FROM organizations")
                org_count = cur.fetchone()[0]
                print(f"✓ ({org_count} organizations)")
            else:
                print("⚠ Table not found")
        
        print()
        print("-" * 40)
        print("✅ Connection successful!")
        print()
        print("Database Info:")
        print(f"  PostgreSQL: {version.split(',')[0]}")
        print(f"  Database:   {info[0]}")
        print(f"  User:       {info[1]}")
        if info[2]:
            print(f"  Server:     {info[2]}:{info[3]}")
        print()
        
    except Exception as e:
        print(f"✗")
        print()
        print("-" * 40)
        print(f"❌ Connection failed!")
        print()
        print(f"Error: {type(e).__name__}: {e}")
        print()
        
        # Provide troubleshooting hints
        error_str = str(e).lower()
        if "could not connect" in error_str or "connection refused" in error_str:
            print("Troubleshooting:")
            print("  - Check if the database host/port are correct")
            print("  - Verify the database server is running")
            print("  - Check firewall rules")
        elif "authentication failed" in error_str or "password" in error_str:
            print("Troubleshooting:")
            print("  - Check database username and password")
            print("  - Verify user has access to the database")
        elif "no such file" in error_str or "permission denied" in error_str:
            print("Troubleshooting:")
            print("  - Check SSH key path is correct")
            print("  - Verify SSH key file permissions (should be 600)")
        elif "ssh" in error_str or "tunnel" in error_str:
            print("Troubleshooting:")
            print("  - Check SSH host and username")
            print("  - Verify SSH key is valid and accepted by the server")
            print("  - Try connecting manually: ssh -i KEY SSH_USER@SSH_HOST")
        
        sys.exit(1)
        
    finally:
        # Clean up
        print("Closing connection...", end=" ", flush=True)
        db.close()
        print("✓")


if __name__ == "__main__":
    test_connection()
