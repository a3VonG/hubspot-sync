"""
Paddle API client.

Fetches subscription/customer data from Paddle for enrichment.
"""

from dataclasses import dataclass
from typing import Optional
import requests


@dataclass
class PaddleSubscription:
    """Paddle subscription/customer data."""
    customer_id: str
    company_name: Optional[str] = None
    vat_number: Optional[str] = None
    country: Optional[str] = None
    email: Optional[str] = None


class PaddleClient:
    """Client for interacting with Paddle API."""
    
    BASE_URL = "https://vendors.paddle.com/api/2.0"
    
    def __init__(self, vendor_id: str, api_key: str):
        """
        Initialize the Paddle client.
        
        Args:
            vendor_id: Paddle vendor ID
            api_key: Paddle API key
        """
        self.vendor_id = vendor_id
        self.api_key = api_key
        self.session = requests.Session()
    
    def _request(self, endpoint: str, data: dict = None) -> dict:
        """Make an API request to Paddle."""
        payload = {
            "vendor_id": self.vendor_id,
            "vendor_auth_code": self.api_key,
        }
        if data:
            payload.update(data)
        
        response = self.session.post(f"{self.BASE_URL}/{endpoint}", data=payload)
        response.raise_for_status()
        result = response.json()
        
        if not result.get("success"):
            raise ValueError(f"Paddle API error: {result.get('error', {})}")
        
        return result.get("response", {})
    
    def get_subscription_by_id(self, paddle_id: str) -> Optional[PaddleSubscription]:
        """
        Get subscription details by Paddle customer/subscription ID.
        
        Args:
            paddle_id: Paddle customer or subscription ID
            
        Returns:
            PaddleSubscription object or None if not found
        """
        try:
            # Try to get user info
            data = self._request("subscription/users", {"subscription_id": paddle_id})
            
            if data and len(data) > 0:
                user = data[0]
                return PaddleSubscription(
                    customer_id=str(user.get("user_id", paddle_id)),
                    company_name=user.get("marketing_consent", {}).get("company_name"),
                    vat_number=user.get("payment_information", {}).get("vat_number"),
                    country=user.get("payment_information", {}).get("country"),
                    email=user.get("user_email"),
                )
        except (requests.HTTPError, ValueError, KeyError):
            pass
        
        return None
    
    def get_customer_by_email(self, email: str) -> Optional[PaddleSubscription]:
        """
        Search for a customer by email.
        
        Args:
            email: Customer email address
            
        Returns:
            PaddleSubscription object or None if not found
        """
        try:
            # List subscriptions and filter by email
            data = self._request("subscription/users", {"plan_id": ""})
            
            for user in data:
                if user.get("user_email", "").lower() == email.lower():
                    return PaddleSubscription(
                        customer_id=str(user.get("user_id", "")),
                        company_name=user.get("marketing_consent", {}).get("company_name"),
                        vat_number=user.get("payment_information", {}).get("vat_number"),
                        country=user.get("payment_information", {}).get("country"),
                        email=user.get("user_email"),
                    )
        except (requests.HTTPError, ValueError, KeyError):
            pass
        
        return None
