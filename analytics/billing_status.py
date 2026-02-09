"""
Billing status computation from Paddle API.

Uses the Paddle Billing API (api.paddle.com) for efficient batch lookups.
"""

from dataclasses import dataclass
from typing import Optional
import requests


@dataclass
class BillingStatus:
    """Billing status for an organization."""
    has_active_subscription: bool = False
    has_any_subscription_history: bool = False
    subscription_status: Optional[str] = None
    
    @property
    def status(self) -> str:
        """
        Billing status as an enum string.
        
        Returns:
            "not started" - no subscription found
            "active"      - subscription active/trialing/past_due
            "cancelled"   - had subscription but now canceled/paused
        """
        if self.has_active_subscription:
            return "active"
        if self.has_any_subscription_history:
            return "cancelled"
        return "not started"
    
    @property
    def is_testing(self) -> bool:
        """
        Determine if organization is in testing mode.
        
        Testing = No valid Paddle subscription AND no subscription history
        (fresh signup running on free credits)
        """
        return not self.has_active_subscription and not self.has_any_subscription_history


class BillingStatusComputer:
    """
    Computes billing status from Paddle API.
    
    Uses Paddle Billing API (api.paddle.com) with batch customer_id filtering
    for efficient lookups.
    """
    
    # Paddle Billing API (not the legacy vendors API)
    BASE_URL = "https://api.paddle.com"
    
    # Subscription states considered "active"
    ACTIVE_STATES = {"active", "trialing", "past_due"}
    
    # Maximum customer IDs per request (Paddle limit)
    BATCH_SIZE = 50
    
    def __init__(self, vendor_id: str, api_key: str):
        """
        Initialize with Paddle credentials.
        
        Args:
            vendor_id: Paddle vendor ID (unused in Billing API, kept for compatibility)
            api_key: Paddle API key (Bearer token for Billing API)
        """
        self.vendor_id = vendor_id
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
    
    def _request_billing_api(self, endpoint: str, params: dict = None) -> dict:
        """Make Paddle Billing API request (GET with query params)."""
        response = self.session.get(
            f"{self.BASE_URL}/{endpoint}",
            params=params,
        )
        response.raise_for_status()
        return response.json()
    
    def _get_subscriptions_for_customers(
        self,
        customer_ids: list[str],
        status: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Fetch subscriptions for a batch of customer IDs.
        
        Args:
            customer_ids: List of Paddle customer IDs (max 50)
            status: Optional list of status filters
            
        Returns:
            List of subscription objects
        """
        if not customer_ids:
            return []
        
        params = {
            "customer_id": ",".join(customer_ids),
            "per_page": 200,
        }
        if status:
            params["status"] = ",".join(status)
        
        all_subscriptions = []
        after = None
        
        while True:
            if after:
                params["after"] = after
            
            try:
                result = self._request_billing_api("subscriptions", params)
                subscriptions = result.get("data", [])
                all_subscriptions.extend(subscriptions)
                
                # Check for pagination
                meta = result.get("meta", {})
                pagination = meta.get("pagination", {})
                if pagination.get("has_more") and pagination.get("next"):
                    after = pagination["next"]
                else:
                    break
            except Exception as e:
                print(f"  Warning: Paddle API error: {e}")
                break
        
        return all_subscriptions
    
    def get_customer_info(
        self,
        customer_id: str,
        need_name: bool = True,
        need_address: bool = True,
        need_business: bool = True,
    ) -> Optional[dict]:
        """
        Get customer info from Paddle Billing API.
        
        Only fetches the endpoints that are actually needed to avoid
        unnecessary API calls. Each flag controls one Paddle API call:
        
        - need_name: GET /customers/{id} (name, email)
        - need_business: GET /customers/{id}/businesses (business name, tax_identifier)
        - need_address: GET /customers/{id}/addresses (country, city, region, postal_code)
        
        Args:
            customer_id: Paddle customer ID
            need_name: Fetch customer name/email
            need_address: Fetch address (country, city, region, postal_code)
            need_business: Fetch business (name override, tax_identifier)
            
        Returns:
            Dict with keys: name, email, country_code, city, region,
            postal_code, tax_identifier (values may be None),
            or None if customer not found.
            
        Raises:
            Exception on API errors for the primary customer lookup.
        """
        if not customer_id:
            return None
        
        info = {
            "name": None,
            "email": None,
            "country_code": None,
            "city": None,
            "region": None,
            "postal_code": None,
            "tax_identifier": None,
        }
        
        # Get customer data (name, email)
        if need_name or need_business:
            # We always need the customer endpoint if name or business is needed,
            # because business name overrides customer name
            result = self._request_billing_api(f"customers/{customer_id}")
            data = result.get("data", {})
            info["name"] = data.get("name")
            info["email"] = data.get("email")
        
        # Get business data (business name override + tax identifier)
        if need_business:
            try:
                biz_result = self._request_billing_api(f"customers/{customer_id}/businesses")
                businesses = biz_result.get("data", [])
                if businesses:
                    biz = businesses[0]
                    biz_name = biz.get("name")
                    if biz_name:
                        info["name"] = biz_name
                    info["tax_identifier"] = biz.get("tax_identifier")
            except Exception as e:
                print(f"  Warning: Could not fetch Paddle business info for {customer_id}: {e}")
        
        # Get address data (country, city, region, postal_code)
        if need_address:
            try:
                addr_result = self._request_billing_api(
                    f"customers/{customer_id}/addresses",
                    {"status": "active", "per_page": 1},
                )
                addresses = addr_result.get("data", [])
                if addresses:
                    addr = addresses[0]
                    info["country_code"] = addr.get("country_code")
                    info["city"] = addr.get("city")
                    info["region"] = addr.get("region")
                    info["postal_code"] = addr.get("postal_code")
            except Exception as e:
                print(f"  Warning: Could not fetch Paddle address info for {customer_id}: {e}")
        
        # Return None only if we have no name at all
        return info if info["name"] else None
    
    def get_billing_status(self, paddle_id: str) -> BillingStatus:
        """
        Get billing status for a single Paddle customer.
        
        Args:
            paddle_id: Paddle customer ID
            
        Returns:
            BillingStatus with subscription info
        """
        if not paddle_id:
            return BillingStatus()
        
        result = self.get_billing_status_batch([paddle_id])
        return result.get(paddle_id, BillingStatus())
    
    def get_billing_status_batch(
        self,
        paddle_ids: list[str],
    ) -> dict[str, BillingStatus]:
        """
        Get billing status for multiple Paddle customers efficiently.
        
        Uses Paddle's batch customer_id filtering to minimize API calls.
        
        Args:
            paddle_ids: List of Paddle customer IDs
            
        Returns:
            Dictionary mapping paddle_id to BillingStatus
        """
        # Filter out empty IDs
        valid_ids = [pid for pid in paddle_ids if pid]
        
        if not valid_ids:
            return {pid: BillingStatus() for pid in paddle_ids}
        
        # Initialize results with defaults
        results = {pid: BillingStatus() for pid in paddle_ids}
        
        # Process in batches
        for i in range(0, len(valid_ids), self.BATCH_SIZE):
            batch = valid_ids[i:i + self.BATCH_SIZE]
            
            try:
                # Fetch all subscriptions for this batch (any status)
                subscriptions = self._get_subscriptions_for_customers(batch)
                
                # Group by customer_id
                customer_subs: dict[str, list[dict]] = {}
                for sub in subscriptions:
                    cid = sub.get("customer_id")
                    if cid:
                        if cid not in customer_subs:
                            customer_subs[cid] = []
                        customer_subs[cid].append(sub)
                
                # Build status for each customer
                for paddle_id in batch:
                    subs = customer_subs.get(paddle_id, [])
                    
                    if subs:
                        results[paddle_id].has_any_subscription_history = True
                        
                        # Check for active subscription
                        for sub in subs:
                            status = sub.get("status", "").lower()
                            if status in self.ACTIVE_STATES:
                                results[paddle_id].has_active_subscription = True
                                results[paddle_id].subscription_status = status
                                break
                        
                        # If no active, record the most recent status
                        if not results[paddle_id].subscription_status and subs:
                            results[paddle_id].subscription_status = subs[0].get("status", "unknown")
                    
            except Exception as e:
                print(f"  Warning: Paddle batch lookup failed: {e}")
                # Continue with other batches
        
        return results
    
    def get_active_customer_ids(self, customer_ids: list[str]) -> set[str]:
        """
        Get which customer IDs from the given list have active subscriptions.
        
        More efficient than get_billing_status_batch when you only need
        active/inactive status.
        
        Args:
            customer_ids: List of Paddle customer IDs to check
            
        Returns:
            Set of customer_ids that have active subscriptions
        """
        valid_ids = [pid for pid in customer_ids if pid]
        
        if not valid_ids:
            return set()
        
        active_ids = set()
        
        # Process in batches
        for i in range(0, len(valid_ids), self.BATCH_SIZE):
            batch = valid_ids[i:i + self.BATCH_SIZE]
            
            try:
                # Only fetch active/trialing/past_due subscriptions
                subscriptions = self._get_subscriptions_for_customers(
                    batch,
                    status=list(self.ACTIVE_STATES),
                )
                
                for sub in subscriptions:
                    cid = sub.get("customer_id")
                    if cid:
                        active_ids.add(cid)
                        
            except Exception as e:
                print(f"  Warning: Paddle active check failed: {e}")
        
        return active_ids
