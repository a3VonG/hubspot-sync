"""
HubSpot API client.

Handles Companies, Contacts, and Tasks through the HubSpot API.
"""

from dataclasses import dataclass, field
from typing import Optional, Any
import requests


@dataclass
class Contact:
    """HubSpot contact."""
    id: str
    email: Optional[str] = None
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    properties: dict = field(default_factory=dict)
    associated_company_ids: list[str] = field(default_factory=list)


@dataclass
class Company:
    """HubSpot company."""
    id: str
    name: Optional[str] = None
    domain: Optional[str] = None
    platform_org_id: Optional[str] = None
    properties: dict = field(default_factory=dict)


@dataclass
class Task:
    """HubSpot task."""
    id: str
    subject: str
    body: Optional[str] = None
    status: str = "NOT_STARTED"


class HubSpotClient:
    """Client for interacting with HubSpot API."""
    
    BASE_URL = "https://api.hubapi.com"
    
    def __init__(self, api_key: str, platform_org_id_property: str = "platform_org_id"):
        """
        Initialize the HubSpot client.
        
        Args:
            api_key: HubSpot private app access token
            platform_org_id_property: Name of the custom property for platform org ID
        """
        self.api_key = api_key
        self.platform_org_id_property = platform_org_id_property
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
    
    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an API request."""
        url = f"{self.BASE_URL}{endpoint}"
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}
    
    # ========== Company Operations ==========
    
    def get_company_by_platform_org_id(
        self,
        platform_org_id: str,
        extra_properties: list[str] = None,
    ) -> Optional[Company]:
        """
        Find a company by platform_org_id custom property.
        
        Args:
            platform_org_id: The platform organization ID
            extra_properties: Additional properties to fetch
            
        Returns:
            Company object or None if not found
        """
        try:
            props_to_fetch = ["name", "domain", self.platform_org_id_property]
            if extra_properties:
                props_to_fetch.extend(extra_properties)
            
            data = self._request(
                "POST",
                "/crm/v3/objects/companies/search",
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": self.platform_org_id_property,
                            "operator": "EQ",
                            "value": platform_org_id,
                        }]
                    }],
                    "properties": props_to_fetch,
                }
            )
            if data.get("results"):
                result = data["results"][0]
                props = result.get("properties", {})
                return Company(
                    id=result["id"],
                    name=props.get("name"),
                    domain=props.get("domain"),
                    platform_org_id=props.get(self.platform_org_id_property),
                    properties=props,
                )
        except requests.HTTPError:
            pass
        return None
    
    def get_company_by_id(
        self,
        company_id: str,
        extra_properties: list[str] = None,
    ) -> Optional[Company]:
        """
        Fetch a company by ID.
        
        Args:
            company_id: HubSpot company ID
            extra_properties: Additional properties to fetch
        """
        try:
            props_to_fetch = [f"name,domain,{self.platform_org_id_property}"]
            if extra_properties:
                props_to_fetch.extend(extra_properties)
            
            data = self._request(
                "GET",
                f"/crm/v3/objects/companies/{company_id}",
                params={"properties": ",".join(props_to_fetch)}
            )
            props = data.get("properties", {})
            return Company(
                id=data["id"],
                name=props.get("name"),
                domain=props.get("domain"),
                platform_org_id=props.get(self.platform_org_id_property),
                properties=props,
            )
        except requests.HTTPError:
            return None
    
    def search_companies_by_domain(self, domain: str) -> list[Company]:
        """
        Search for companies by domain.
        
        Args:
            domain: The domain to search for
            
        Returns:
            List of matching companies
        """
        try:
            data = self._request(
                "POST",
                "/crm/v3/objects/companies/search",
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "domain",
                            "operator": "EQ",
                            "value": domain,
                        }]
                    }],
                    "properties": ["name", "domain", self.platform_org_id_property],
                    "limit": 100,
                }
            )
            companies = []
            for result in data.get("results", []):
                props = result.get("properties", {})
                companies.append(Company(
                    id=result["id"],
                    name=props.get("name"),
                    domain=props.get("domain"),
                    platform_org_id=props.get(self.platform_org_id_property),
                    properties=props,
                ))
            return companies
        except requests.HTTPError:
            return []
    
    def get_all_companies_with_platform_org_id(
        self,
        extra_properties: list[str] = None,
    ) -> list[Company]:
        """
        Get all companies that have a platform_org_id set.
        
        Uses HubSpot search API with pagination to fetch all linked companies.
        This is the starting point for analytics-only sync.
        
        Args:
            extra_properties: Additional properties to fetch
            
        Returns:
            List of companies with platform_org_id
        """
        companies = []
        after = None
        
        # Build properties list
        props_to_fetch = ["name", "domain", self.platform_org_id_property]
        if extra_properties:
            props_to_fetch.extend(extra_properties)
        
        while True:
            try:
                body = {
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": self.platform_org_id_property,
                            "operator": "HAS_PROPERTY",
                        }]
                    }],
                    "properties": props_to_fetch,
                    "limit": 100,
                }
                if after:
                    body["after"] = after
                
                data = self._request(
                    "POST",
                    "/crm/v3/objects/companies/search",
                    json=body
                )
                
                for result in data.get("results", []):
                    props = result.get("properties", {})
                    org_id = props.get(self.platform_org_id_property)
                    # Filter out empty values
                    if org_id and org_id.strip():
                        companies.append(Company(
                            id=result["id"],
                            name=props.get("name"),
                            domain=props.get("domain"),
                            platform_org_id=org_id,
                            properties=props,
                        ))
                
                # Check for more pages
                paging = data.get("paging", {})
                next_page = paging.get("next", {})
                after = next_page.get("after")
                
                if not after:
                    break
                    
            except requests.HTTPError:
                break
        
        return companies
    
    def search_companies_by_name(self, name: str) -> list[Company]:
        """
        Search for companies by name (contains).
        
        Args:
            name: The company name to search for
            
        Returns:
            List of matching companies
        """
        try:
            data = self._request(
                "POST",
                "/crm/v3/objects/companies/search",
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "name",
                            "operator": "CONTAINS_TOKEN",
                            "value": name,
                        }]
                    }],
                    "properties": ["name", "domain", self.platform_org_id_property],
                    "limit": 100,
                }
            )
            companies = []
            for result in data.get("results", []):
                props = result.get("properties", {})
                companies.append(Company(
                    id=result["id"],
                    name=props.get("name"),
                    domain=props.get("domain"),
                    platform_org_id=props.get(self.platform_org_id_property),
                    properties=props,
                ))
            return companies
        except requests.HTTPError:
            return []
    
    def update_company_platform_org_id(self, company_id: str, platform_org_id: str) -> bool:
        """
        Set the platform_org_id on a company.
        
        Args:
            company_id: HubSpot company ID
            platform_org_id: Platform organization ID to set
            
        Returns:
            True if successful
        """
        try:
            self._request(
                "PATCH",
                f"/crm/v3/objects/companies/{company_id}",
                json={
                    "properties": {
                        self.platform_org_id_property: platform_org_id,
                    }
                }
            )
            return True
        except requests.HTTPError:
            return False
    
    def create_company(self, properties: dict) -> Optional[Company]:
        """
        Create a new company.
        
        Args:
            properties: Dictionary of company properties
            
        Returns:
            Created Company object or None if failed
        """
        try:
            data = self._request(
                "POST",
                "/crm/v3/objects/companies",
                json={"properties": properties}
            )
            props = data.get("properties", {})
            return Company(
                id=data["id"],
                name=props.get("name"),
                domain=props.get("domain"),
                platform_org_id=props.get(self.platform_org_id_property),
                properties=props,
            )
        except requests.HTTPError:
            return None
    
    def update_company(self, company_id: str, properties: dict) -> tuple[bool, str]:
        """
        Update company properties.
        
        Args:
            company_id: HubSpot company ID
            properties: Dictionary of properties to update
            
        Returns:
            Tuple of (success, error_message). error_message is empty on success.
        """
        try:
            self._request(
                "PATCH",
                f"/crm/v3/objects/companies/{company_id}",
                json={"properties": properties}
            )
            return True, ""
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body = ""
            try:
                body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            error_msg = f"HTTP {status}: {body}" if body else f"HTTP {status}"
            return False, error_msg
    
    def get_company_with_source(self, company_id: str, source_property: str) -> Optional[Company]:
        """
        Fetch a company including the source property.
        
        Args:
            company_id: HubSpot company ID
            source_property: Name of the company source property
            
        Returns:
            Company object or None if not found
        """
        try:
            data = self._request(
                "GET",
                f"/crm/v3/objects/companies/{company_id}",
                params={"properties": f"name,domain,{self.platform_org_id_property},{source_property}"}
            )
            props = data.get("properties", {})
            return Company(
                id=data["id"],
                name=props.get("name"),
                domain=props.get("domain"),
                platform_org_id=props.get(self.platform_org_id_property),
                properties=props,
            )
        except requests.HTTPError:
            return None
    
    # ========== Contact Operations ==========
    
    def get_contact_by_email(self, email: str) -> Optional[Contact]:
        """
        Find a contact by email.
        
        Args:
            email: The email address to search for
            
        Returns:
            Contact object or None if not found
        """
        try:
            data = self._request(
                "POST",
                "/crm/v3/objects/contacts/search",
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email,
                        }]
                    }],
                    "properties": ["email", "firstname", "lastname"],
                }
            )
            if data.get("results"):
                result = data["results"][0]
                props = result.get("properties", {})
                contact = Contact(
                    id=result["id"],
                    email=props.get("email"),
                    firstname=props.get("firstname"),
                    lastname=props.get("lastname"),
                    properties=props,
                )
                # Fetch associated companies
                contact.associated_company_ids = self.get_contact_company_associations(contact.id)
                return contact
        except requests.HTTPError:
            pass
        return None
    
    def get_contacts_by_emails(self, emails: list[str]) -> list[Contact]:
        """
        Find contacts by a list of emails.
        
        Args:
            emails: List of email addresses to search for
            
        Returns:
            List of found contacts
        """
        contacts = []
        # HubSpot search doesn't support OR, so we batch requests
        for email in emails:
            contact = self.get_contact_by_email(email)
            if contact:
                contacts.append(contact)
        return contacts
    
    def create_contact(self, email: str, firstname: Optional[str] = None, 
                       lastname: Optional[str] = None) -> Optional[Contact]:
        """
        Create a new contact.
        
        Args:
            email: Contact email
            firstname: Contact first name
            lastname: Contact last name
            
        Returns:
            Created Contact object or None if failed
        """
        properties = {"email": email}
        if firstname:
            properties["firstname"] = firstname
        if lastname:
            properties["lastname"] = lastname
        
        try:
            data = self._request(
                "POST",
                "/crm/v3/objects/contacts",
                json={"properties": properties}
            )
            props = data.get("properties", {})
            return Contact(
                id=data["id"],
                email=props.get("email"),
                firstname=props.get("firstname"),
                lastname=props.get("lastname"),
                properties=props,
            )
        except requests.HTTPError:
            return None
    
    def get_contact_company_associations(self, contact_id: str) -> list[str]:
        """
        Get company IDs associated with a contact.
        
        Args:
            contact_id: HubSpot contact ID
            
        Returns:
            List of associated company IDs
        """
        try:
            data = self._request(
                "GET",
                f"/crm/v4/objects/contacts/{contact_id}/associations/companies"
            )
            return [
                str(assoc["toObjectId"]) 
                for assoc in data.get("results", [])
            ]
        except requests.HTTPError:
            return []
    
    def associate_contact_with_company(self, contact_id: str, company_id: str) -> bool:
        """
        Create an association between a contact and a company.
        
        Args:
            contact_id: HubSpot contact ID
            company_id: HubSpot company ID
            
        Returns:
            True if successful
        """
        try:
            self._request(
                "PUT",
                f"/crm/v4/objects/contacts/{contact_id}/associations/companies/{company_id}",
                json=[{
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 1  # Contact to Company
                }]
            )
            return True
        except requests.HTTPError:
            return False
    
    # ========== Task Operations ==========
    
    def create_task(self, subject: str, body: str, 
                    associated_company_id: Optional[str] = None,
                    associated_contact_id: Optional[str] = None) -> Optional[Task]:
        """
        Create a HubSpot task.
        
        Args:
            subject: Task subject/title
            body: Task body/description
            associated_company_id: Optional company to associate with
            associated_contact_id: Optional contact to associate with
            
        Returns:
            Created Task object or None if failed
        """
        try:
            # Create the task
            data = self._request(
                "POST",
                "/crm/v3/objects/tasks",
                json={
                    "properties": {
                        "hs_task_subject": subject,
                        "hs_task_body": body,
                        "hs_task_status": "NOT_STARTED",
                        "hs_task_type": "TODO",
                    }
                }
            )
            
            task_id = data["id"]
            
            # Associate with company if provided
            if associated_company_id:
                try:
                    self._request(
                        "PUT",
                        f"/crm/v4/objects/tasks/{task_id}/associations/companies/{associated_company_id}",
                        json=[{
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 192  # Task to Company
                        }]
                    )
                except requests.HTTPError:
                    pass  # Non-critical
            
            # Associate with contact if provided
            if associated_contact_id:
                try:
                    self._request(
                        "PUT",
                        f"/crm/v4/objects/tasks/{task_id}/associations/contacts/{associated_contact_id}",
                        json=[{
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 204  # Task to Contact
                        }]
                    )
                except requests.HTTPError:
                    pass  # Non-critical
            
            return Task(
                id=task_id,
                subject=subject,
                body=body,
            )
        except requests.HTTPError:
            return None
    
    def search_tasks_by_subject(self, subject_contains: str) -> list[Task]:
        """
        Search for tasks by subject.
        
        Args:
            subject_contains: Text to search for in task subject
            
        Returns:
            List of matching tasks
        """
        try:
            data = self._request(
                "POST",
                "/crm/v3/objects/tasks/search",
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "hs_task_subject",
                            "operator": "CONTAINS_TOKEN",
                            "value": subject_contains,
                        }]
                    }],
                    "properties": ["hs_task_subject", "hs_task_body", "hs_task_status"],
                    "limit": 100,
                }
            )
            tasks = []
            for result in data.get("results", []):
                props = result.get("properties", {})
                tasks.append(Task(
                    id=result["id"],
                    subject=props.get("hs_task_subject", ""),
                    body=props.get("hs_task_body"),
                    status=props.get("hs_task_status", "NOT_STARTED"),
                ))
            return tasks
        except requests.HTTPError:
            return []
