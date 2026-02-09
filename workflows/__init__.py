"""
HubSpot Workflow Management.

Collaborative fetch → edit → preview → update cycle for HubSpot workflows.

Usage:
    python -m workflows fetch --folder "Standard Labs"
    python -m workflows preview
    python -m workflows update
"""

from .client import WorkflowClient, Workflow, WorkflowSummary
from .manager import WorkflowManager

__all__ = ["WorkflowClient", "Workflow", "WorkflowSummary", "WorkflowManager"]
