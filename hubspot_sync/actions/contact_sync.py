"""
Contact synchronization actions.

Creates HubSpot contacts and associates them with companies.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..clients.hubspot import HubSpotClient, Company, Contact
from ..clients.platform import Organization, User
from ..config import Config
from ..utils.audit import AuditLog, SyncEventType


@dataclass
class ContactSyncResult:
    """Result of syncing contacts for an organization."""
    organization: Organization
    company: Company
    contacts_created: list[Contact] = field(default_factory=list)
    contacts_associated: list[Contact] = field(default_factory=list)
    contacts_already_associated: list[Contact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class ContactSyncer:
    """
    Synchronizes platform users as HubSpot contacts.
    
    Creates contacts if they don't exist and associates them
    with the linked company.
    """
    
    def __init__(
        self,
        hubspot: HubSpotClient,
        config: Config,
        audit_log: AuditLog,
    ):
        """
        Initialize the contact syncer.
        
        Args:
            hubspot: HubSpot API client
            config: Configuration
            audit_log: Audit logger
        """
        self.hubspot = hubspot
        self.config = config
        self.audit_log = audit_log
    
    def sync_organization_contacts(
        self,
        org: Organization,
        company: Company,
    ) -> ContactSyncResult:
        """
        Sync all users in an organization as contacts.
        
        Args:
            org: Platform organization with users
            company: HubSpot company to associate contacts with
            
        Returns:
            ContactSyncResult with details
        """
        result = ContactSyncResult(organization=org, company=company)
        
        for user in org.users:
            if not user.email:
                continue
            
            try:
                self._sync_user_contact(user, company, result)
            except Exception as e:
                error_msg = f"Error syncing {user.email}: {str(e)}"
                result.errors.append(error_msg)
                self.audit_log.log(
                    SyncEventType.ERROR,
                    message=error_msg,
                    platform_org_id=org.id,
                    platform_org_name=org.name,
                    email=user.email,
                )
        
        return result
    
    # Property set on contacts whose email exists as a platform user
    HAS_ACCOUNT_PROPERTY = "platform_email_has_account"

    def _sync_user_contact(
        self,
        user: User,
        company: Company,
        result: ContactSyncResult,
    ) -> Optional[Contact]:
        """Sync a single user as a contact."""
        # Find existing contact (include has-account property so we can skip no-op updates)
        contact = self.hubspot.get_contact_by_email(
            user.email,
            extra_properties=[self.HAS_ACCOUNT_PROPERTY],
        )
        
        if not contact:
            # Create new contact with platform_email_has_account already set
            if self.config.dry_run:
                self.audit_log.log(
                    SyncEventType.CONTACT_CREATED,
                    message=f"[DRY RUN] Would create contact for {user.email}",
                    platform_org_id=result.organization.id,
                    platform_org_name=result.organization.name,
                    email=user.email,
                )
                return None
            
            contact = self.hubspot.create_contact(
                email=user.email,
                firstname=user.first_name,
                lastname=user.last_name,
                extra_properties={self.HAS_ACCOUNT_PROPERTY: "true"},
            )
            
            if contact:
                result.contacts_created.append(contact)
                self.audit_log.log(
                    SyncEventType.CONTACT_CREATED,
                    message=f"Created contact for {user.email}",
                    platform_org_id=result.organization.id,
                    platform_org_name=result.organization.name,
                    hubspot_contact_id=contact.id,
                    email=user.email,
                )
            else:
                result.errors.append(f"Failed to create contact for {user.email}")
                return None
        else:
            # Existing contact — ensure platform_email_has_account is set
            self._ensure_has_account_property(contact, result)
        
        # Check if already associated with this company
        if contact and company.id in contact.associated_company_ids:
            result.contacts_already_associated.append(contact)
            self.audit_log.log(
                SyncEventType.SKIPPED,
                message=f"Contact {user.email} already associated with {company.name}",
                platform_org_id=result.organization.id,
                platform_org_name=result.organization.name,
                hubspot_company_id=company.id,
                hubspot_company_name=company.name,
                hubspot_contact_id=contact.id,
                email=user.email,
            )
            return contact
        
        # Associate contact with company
        if contact:
            if self.config.dry_run:
                self.audit_log.log(
                    SyncEventType.CONTACT_ASSOCIATED,
                    message=f"[DRY RUN] Would associate {user.email} with {company.name}",
                    platform_org_id=result.organization.id,
                    platform_org_name=result.organization.name,
                    hubspot_company_id=company.id,
                    hubspot_company_name=company.name,
                    hubspot_contact_id=contact.id,
                    email=user.email,
                )
                return contact
            
            success = self.hubspot.associate_contact_with_company(contact.id, company.id)
            
            if success:
                result.contacts_associated.append(contact)
                self.audit_log.log(
                    SyncEventType.CONTACT_ASSOCIATED,
                    message=f"Associated {user.email} with {company.name}",
                    platform_org_id=result.organization.id,
                    platform_org_name=result.organization.name,
                    hubspot_company_id=company.id,
                    hubspot_company_name=company.name,
                    hubspot_contact_id=contact.id,
                    email=user.email,
                )
            else:
                result.errors.append(f"Failed to associate {user.email} with company")
        
        return contact
    
    def _ensure_has_account_property(
        self,
        contact: Contact,
        result: ContactSyncResult,
    ) -> None:
        """Set platform_email_has_account=true on an existing contact if not already set."""
        current = (contact.properties.get(self.HAS_ACCOUNT_PROPERTY) or "").strip().lower()
        if current == "true":
            return
        
        if self.config.dry_run:
            self.audit_log.log(
                SyncEventType.SKIPPED,
                message=f"[DRY RUN] Would set {self.HAS_ACCOUNT_PROPERTY}=true on {contact.email}",
                platform_org_id=result.organization.id,
                platform_org_name=result.organization.name,
                hubspot_contact_id=contact.id,
                email=contact.email,
            )
            return
        
        ok, err = self.hubspot.update_contact(
            contact.id,
            {self.HAS_ACCOUNT_PROPERTY: "true"},
        )
        if not ok:
            result.errors.append(
                f"Failed to set {self.HAS_ACCOUNT_PROPERTY} on {contact.email}: {err}"
            )