"""
HubSpot Automation v4 API client for workflow operations.

Endpoints used:
  GET  /automation/v4/flows              - List all workflows (summary)
  GET  /automation/v4/flows/{flowId}     - Get full workflow details
  POST /automation/v4/flows/batch/read   - Batch read workflows
  POST /automation/v4/flows              - Create a workflow
  PUT  /automation/v4/flows/{flowId}     - Update a workflow
  DELETE /automation/v4/flows/{flowId}   - Delete a workflow

Scope required: `automation`
"""

import time
from dataclasses import dataclass, field
from typing import Optional

import requests


@dataclass
class WorkflowSummary:
    """Summary of a HubSpot workflow (from the list endpoint)."""
    id: str
    name: str = ""
    is_enabled: bool = False
    object_type_id: str = ""
    revision_id: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Workflow:
    """Full HubSpot workflow definition."""
    id: str
    name: str = ""
    is_enabled: bool = False
    flow_type: str = ""
    revision_id: str = ""
    object_type_id: str = ""
    type: str = ""
    created_at: str = ""
    updated_at: str = ""
    actions: list = field(default_factory=list)
    enrollment_criteria: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class WorkflowClient:
    """Client for HubSpot Automation v4 API."""

    BASE_URL = "https://api.hubapi.com"

    def __init__(self, api_key: str):
        """
        Initialize the workflow client.

        Args:
            api_key: HubSpot private app access token (needs `automation` scope)
        """
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """
        Make an API request with basic rate-limit handling.

        Retries once on HTTP 429 (rate limit) using the Retry-After header.
        """
        url = f"{self.BASE_URL}{endpoint}"
        response = self.session.request(method, url, **kwargs)

        # Handle rate limiting
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 10))
            print(f"  Rate limited – waiting {retry_after}s …")
            time.sleep(retry_after)
            response = self.session.request(method, url, **kwargs)

        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    # ========== List / Read ==========

    def list_workflows(self, folder_id: str | None = None) -> list[WorkflowSummary]:
        """
        Fetch all workflows (summary view).

        The v4 list endpoint returns key fields: id, isEnabled, objectTypeId,
        revisionId. Name may or may not be included depending on HubSpot version.

        Args:
            folder_id: Optional HubSpot folder ID to filter by.
        """
        params = {}
        if folder_id:
            params["folderId"] = folder_id

        data = self._request("GET", "/automation/v4/flows", params=params or None)

        # Response may be {"flows": [...]} or {"results": [...]}
        flows = data.get("flows", data.get("results", []))

        workflows = []
        for flow in flows:
            workflows.append(WorkflowSummary(
                id=str(flow.get("id", "")),
                name=flow.get("name", ""),
                is_enabled=flow.get("isEnabled", False),
                object_type_id=flow.get("objectTypeId", ""),
                revision_id=str(flow.get("revisionId", "")),
                raw=flow,
            ))
        return workflows

    def get_workflow(self, flow_id: str) -> Workflow:
        """Fetch full workflow details by flow ID."""
        data = self._request("GET", f"/automation/v4/flows/{flow_id}")
        return self._parse_workflow(data)

    def get_workflows_batch(self, flow_ids: list[str]) -> list[Workflow]:
        """
        Fetch multiple workflows by ID in a single request.

        Uses POST /automation/v4/flows/batch/read.
        """
        inputs = [{"flowId": fid, "type": "FLOW_ID"} for fid in flow_ids]
        data = self._request(
            "POST",
            "/automation/v4/flows/batch/read",
            json={"inputs": inputs},
        )

        workflows = []
        for flow in data.get("results", []):
            workflows.append(self._parse_workflow(flow))
        return workflows

    # ========== Create / Update / Delete ==========

    def create_workflow(self, spec: dict) -> Workflow:
        """
        Create a new workflow.

        Args:
            spec: Full workflow specification (see HubSpot v4 docs).
        """
        data = self._request("POST", "/automation/v4/flows", json=spec)
        return self._parse_workflow(data)

    def update_workflow(self, flow_id: str, spec: dict) -> Workflow:
        """
        Update an existing workflow.

        The spec MUST include `revisionId` (latest) and `type`.
        Fetch the current workflow first to obtain the latest revisionId.
        """
        data = self._request("PUT", f"/automation/v4/flows/{flow_id}", json=spec)
        return self._parse_workflow(data)

    def delete_workflow(self, flow_id: str) -> bool:
        """
        Delete a workflow.  WARNING: irreversible via API.

        Contact HubSpot support to restore a deleted workflow.
        """
        self._request("DELETE", f"/automation/v4/flows/{flow_id}")
        return True

    # ========== Helpers ==========

    @staticmethod
    def _parse_workflow(data: dict) -> Workflow:
        """Parse a raw workflow API response into a Workflow dataclass."""
        return Workflow(
            id=str(data.get("id", "")),
            name=data.get("name", ""),
            is_enabled=data.get("isEnabled", False),
            flow_type=data.get("flowType", ""),
            revision_id=str(data.get("revisionId", "")),
            object_type_id=data.get("objectTypeId", ""),
            type=data.get("type", ""),
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            actions=data.get("actions", []),
            enrollment_criteria=data.get("enrollmentCriteria", {}),
            raw=data,
        )
