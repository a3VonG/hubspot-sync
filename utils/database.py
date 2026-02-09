"""
Database connection utilities with SSH tunnel support.
"""

import base64
import os
import subprocess
import tempfile
from contextlib import contextmanager
from typing import Optional, Generator

import psycopg2
from psycopg2.extensions import connection as PgConnection

from config import DatabaseConfig


def _resolve_1password_reference(reference: str) -> str:
    """
    Resolve a 1Password CLI reference (op://...) to its value.
    
    Args:
        reference: 1Password reference like "op://vault/item/field"
        
    Returns:
        The resolved secret value
        
    Raises:
        RuntimeError: If 1Password CLI fails
    """
    try:
        result = subprocess.run(
            ["op", "read", reference],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError(
            "1Password CLI (op) not found. Install it from: "
            "https://developer.1password.com/docs/cli/get-started/"
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to read from 1Password: {e.stderr.strip()}\n"
            "Make sure you're signed in: op signin"
        )


class SSHTunnel:
    """SSH tunnel manager for database connections."""
    
    def __init__(self, db_config: DatabaseConfig):
        """
        Initialize SSH tunnel.
        
        Args:
            db_config: Database configuration with SSH settings
        """
        self.db_config = db_config
        self._tunnel = None
        self._temp_key_file = None
    
    def _get_ssh_key_path(self) -> str:
        """Get the path to the SSH private key."""
        # If base64-encoded key is provided, decode it to a temp file
        if self.db_config.ssh_key_base64:
            key_data = self.db_config.ssh_key_base64
            
            # Check if it's a 1Password reference
            if key_data.startswith("op://"):
                key_data = _resolve_1password_reference(key_data)
            
            # Decode base64 if needed (1Password returns raw, env var might be base64)
            try:
                key_bytes = base64.b64decode(key_data)
            except Exception:
                # Not base64, use as-is (raw key from 1Password)
                key_bytes = key_data.encode('utf-8')
            
            self._temp_key_file = tempfile.NamedTemporaryFile(
                mode='wb',
                delete=False,
                suffix='_ssh_key'
            )
            self._temp_key_file.write(key_bytes)
            self._temp_key_file.close()
            os.chmod(self._temp_key_file.name, 0o600)
            return self._temp_key_file.name
        
        # Check if path is a 1Password reference
        if self.db_config.ssh_key_path:
            if self.db_config.ssh_key_path.startswith("op://"):
                # Resolve from 1Password and write to temp file
                key_content = _resolve_1password_reference(self.db_config.ssh_key_path)
                self._temp_key_file = tempfile.NamedTemporaryFile(
                    mode='w',
                    delete=False,
                    suffix='_ssh_key'
                )
                self._temp_key_file.write(key_content)
                self._temp_key_file.close()
                os.chmod(self._temp_key_file.name, 0o600)
                return self._temp_key_file.name
            
            # Regular file path
            return os.path.expanduser(self.db_config.ssh_key_path)
        
        raise ValueError("No SSH key provided (SSH_KEY_PATH or SSH_KEY_BASE64)")
    
    def start(self) -> int:
        """
        Start the SSH tunnel.
        
        Returns:
            Local port number for the tunnel
        """
        try:
            from sshtunnel import SSHTunnelForwarder
        except ImportError:
            raise ImportError(
                "sshtunnel package required for SSH tunneling. "
                "Install with: pip install sshtunnel"
            )
        
        ssh_key_path = self._get_ssh_key_path()
        
        self._tunnel = SSHTunnelForwarder(
            (self.db_config.ssh_host, 22),
            ssh_username=self.db_config.ssh_user,
            ssh_pkey=ssh_key_path,
            remote_bind_address=(self.db_config.host, self.db_config.port),
            local_bind_address=('127.0.0.1', 0),  # Auto-assign local port
        )
        
        self._tunnel.start()
        return self._tunnel.local_bind_port
    
    def stop(self):
        """Stop the SSH tunnel and clean up."""
        if self._tunnel:
            self._tunnel.stop()
            self._tunnel = None
        
        # Clean up temp key file
        if self._temp_key_file and os.path.exists(self._temp_key_file.name):
            os.unlink(self._temp_key_file.name)
            self._temp_key_file = None


class DatabaseConnection:
    """
    Database connection manager with optional SSH tunnel.
    
    Usage:
        db = DatabaseConnection(db_config)
        db.connect()
        conn = db.connection
        # ... use connection ...
        db.close()
    
    Or as context manager:
        with DatabaseConnection(db_config) as conn:
            # ... use connection ...
    """
    
    def __init__(self, db_config: DatabaseConfig):
        """
        Initialize database connection manager.
        
        Args:
            db_config: Database configuration
        """
        self.db_config = db_config
        self._tunnel: Optional[SSHTunnel] = None
        self._connection: Optional[PgConnection] = None
    
    @property
    def connection(self) -> PgConnection:
        """Get the database connection."""
        if not self._connection or self._connection.closed:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._connection
    
    def connect(self) -> PgConnection:
        """
        Establish database connection (with SSH tunnel if configured).
        
        Returns:
            psycopg2 connection object
        """
        host = self.db_config.host
        port = self.db_config.port
        
        # Start SSH tunnel if required
        if self.db_config.requires_tunnel:
            self._tunnel = SSHTunnel(self.db_config)
            local_port = self._tunnel.start()
            host = '127.0.0.1'
            port = local_port
        
        # Connect to database
        self._connection = psycopg2.connect(
            host=host,
            port=port,
            database=self.db_config.name,
            user=self.db_config.user,
            password=self.db_config.password,
        )
        
        return self._connection
    
    def close(self):
        """Close database connection and SSH tunnel."""
        if self._connection and not self._connection.closed:
            self._connection.close()
            self._connection = None
        
        if self._tunnel:
            self._tunnel.stop()
            self._tunnel = None
    
    def __enter__(self) -> PgConnection:
        """Context manager entry."""
        return self.connect()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


@contextmanager
def get_db_connection(db_config: DatabaseConfig) -> Generator[PgConnection, None, None]:
    """
    Context manager for database connections.
    
    Args:
        db_config: Database configuration
        
    Yields:
        psycopg2 connection object
    """
    db = DatabaseConnection(db_config)
    try:
        yield db.connect()
    finally:
        db.close()
