"""
Platform database client.

Fetches organizations and users from the platform's PostgreSQL database.
"""

from dataclasses import dataclass
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor


@dataclass
class User:
    """Platform user."""
    id: str
    email: str
    organization_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class Organization:
    """Platform organization."""
    id: str
    name: str
    admin_user_id: Optional[str] = None
    paddle_id: Optional[str] = None
    users: list[User] = None
    
    def __post_init__(self):
        if self.users is None:
            self.users = []
    
    @property
    def admin_email(self) -> Optional[str]:
        """Get the admin user's email."""
        for user in self.users:
            if user.id == self.admin_user_id:
                return user.email
        return None
    
    @property
    def user_emails(self) -> list[str]:
        """Get all user emails in this organization."""
        return [user.email for user in self.users if user.email]


class PlatformClient:
    """Client for fetching data from the platform database."""
    
    def __init__(self, db_url: str):
        """
        Initialize the platform client.
        
        Args:
            db_url: PostgreSQL connection string
        """
        self.db_url = db_url
        self._conn = None
    
    def _get_connection(self):
        """Get or create a database connection."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.db_url)
        return self._conn
    
    def close(self):
        """Close the database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
    
    def get_all_organizations(self) -> list[Organization]:
        """
        Fetch all organizations with their users.
        
        Returns:
            List of Organization objects with users populated
        """
        conn = self._get_connection()
        
        # Fetch all organizations
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    id,
                    name,
                    admin_user_id,
                    paddle_id
                FROM organizations
                WHERE is_default_organization = false
                ORDER BY name
            """)
            org_rows = cur.fetchall()
        
        # Fetch all users
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    id,
                    email,
                    organization_id,
                    first_name,
                    last_name,
                    created_at
                FROM users
                WHERE organization_id IS NOT NULL
                ORDER BY created_at
            """)
            user_rows = cur.fetchall()
        
        # Group users by organization
        users_by_org: dict[str, list[User]] = {}
        for row in user_rows:
            user = User(
                id=str(row["id"]),
                email=row["email"],
                organization_id=str(row["organization_id"]),
                first_name=row.get("first_name"),
                last_name=row.get("last_name"),
                created_at=str(row["created_at"]) if row.get("created_at") else None,
            )
            org_id = str(row["organization_id"])
            if org_id not in users_by_org:
                users_by_org[org_id] = []
            users_by_org[org_id].append(user)
        
        # Build organization objects
        organizations = []
        for row in org_rows:
            org_id = str(row["id"])
            org = Organization(
                id=org_id,
                name=row["name"] or "",
                admin_user_id=str(row["admin_user_id"]) if row.get("admin_user_id") else None,
                paddle_id=row.get("paddle_id"),
                users=users_by_org.get(org_id, []),
            )
            organizations.append(org)
        
        return organizations
    
    def get_organization_by_id(self, org_id: str) -> Optional[Organization]:
        """
        Fetch a single organization by ID.
        
        Args:
            org_id: The organization UUID
            
        Returns:
            Organization object or None if not found
        """
        conn = self._get_connection()
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    id,
                    name,
                    admin_user_id,
                    paddle_id
                FROM organizations
                WHERE id = %s
            """, (org_id,))
            row = cur.fetchone()
        
        if not row:
            return None
        
        # Fetch users for this organization
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    id,
                    email,
                    organization_id,
                    first_name,
                    last_name,
                    created_at
                FROM users
                WHERE organization_id = %s
                ORDER BY created_at
            """, (org_id,))
            user_rows = cur.fetchall()
        
        users = [
            User(
                id=str(u["id"]),
                email=u["email"],
                organization_id=str(u["organization_id"]),
                first_name=u.get("first_name"),
                last_name=u.get("last_name"),
                created_at=str(u["created_at"]) if u.get("created_at") else None,
            )
            for u in user_rows
        ]
        
        return Organization(
            id=str(row["id"]),
            name=row["name"] or "",
            admin_user_id=str(row["admin_user_id"]) if row.get("admin_user_id") else None,
            paddle_id=row.get("paddle_id"),
            users=users,
        )
