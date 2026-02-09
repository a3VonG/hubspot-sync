"""
High-level workflow manager.

Implements a collaborative fetch → edit → preview → update cycle:

  workflows/data/
    original.json   ← fetched from HubSpot (untouched reference)
    working.json    ← our editable copy (edit together in chat)

Commands:
  fetch   → pulls from HubSpot, writes both original.json and working.json
  preview → diffs working.json vs original.json, shows what would change
  update  → pushes only changed workflows from working.json to HubSpot
"""

import json
import os
from datetime import datetime

from .client import WorkflowClient, Workflow, WorkflowSummary


# ── Human-readable lookups ─────────────────────────────────────────────────

ACTION_TYPE_NAMES = {
    "0-1": "Delay",
    "0-3": "Create Task",
    "0-4": "Send Automated Email",
    "0-5": "Set Property",
    "0-8": "Send Email Notification",
    "0-9": "Send In-App Notification",
    "0-14": "Create Record",
    "0-35": "Delay Until Date",
    "0-63809083": "Add to Static List",
    "0-63863438": "Remove from Static List",
}

EVENT_TYPE_NAMES = {
    "4-1553675": "Ad Interaction",
    "4-666440": "Email Open",
    "4-665538": "Email Reply",
    "4-666288": "Email Click",
    "4-665536": "Email Delivery",
    "4-1639801": "Form Submission",
    "4-1639797": "Form View",
    "4-1639799": "Form Interaction",
    "4-68559": "Marketing Event Registration",
    "4-69072": "Marketing Event Cancellation",
    "4-1733817": "Call Start",
    "4-1741072": "Call End",
    "4-1722276": "SMS Shortlink Click",
    "4-1555804": "CTA View",
    "4-1555805": "CTA Click",
    "4-675783": "Media Play on Webpage",
}

OBJECT_TYPE_NAMES = {
    "0-1": "Contact",
    "0-2": "Company",
    "0-3": "Deal",
    "0-5": "Ticket",
}

# Keys to compare when diffing (ignoring noisy fields like updatedAt)
_DIFF_IGNORE_KEYS = {"updatedAt", "revisionId", "crmObjectCreationStatus"}


class WorkflowManager:
    """
    Manages the collaborative workflow cycle.

    Data lives in workflows/data/:
      original.json  – what was last fetched from HubSpot
      working.json   – the copy you edit together in chat
    """

    ORIGINAL_FILE = "original.json"
    WORKING_FILE = "working.json"

    def __init__(self, client: WorkflowClient, data_dir: str | None = None):
        self.client = client
        self.data_dir = data_dir or os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(self.data_dir, exist_ok=True)

    @property
    def original_path(self) -> str:
        return os.path.join(self.data_dir, self.ORIGINAL_FILE)

    @property
    def working_path(self) -> str:
        return os.path.join(self.data_dir, self.WORKING_FILE)

    # ══════════════════════════════════════════════════════════════════════════
    #  FETCH  –  pull from HubSpot → save original.json + working.json
    # ══════════════════════════════════════════════════════════════════════════

    def fetch(
        self,
        folder: str | None = None,
        folder_id: str | None = None,
        name_filter: str | None = None,
        fetch_all: bool = False,
    ) -> list[Workflow]:
        """
        Fetch workflows from HubSpot and save to original.json + working.json.

        At least one of folder, folder_id, name_filter, or fetch_all must be specified.
        """
        if folder_id:
            workflows = self._fetch_by_folder_id(folder_id)
        elif folder:
            workflows = self._fetch_by_folder(folder)
        elif name_filter:
            workflows = self._fetch_by_name(name_filter)
        elif fetch_all:
            workflows = self._fetch_all_full()
        else:
            raise ValueError("Specify folder_id, folder, name_filter, or fetch_all=True")

        if not workflows:
            print("No workflows found.")
            return []

        # Save both files identically
        envelope = {
            "fetched_at": datetime.now().isoformat(),
            "source": folder_id or folder or name_filter or "all",
            "count": len(workflows),
            "workflows": [wf.raw for wf in workflows],
        }

        for path in (self.original_path, self.working_path):
            with open(path, "w") as f:
                json.dump(envelope, f, indent=2)

        print(f"\n  Saved {len(workflows)} workflows:")
        print(f"    {self.original_path}  (reference – do not edit)")
        print(f"    {self.working_path}  (our working copy – edit this)")
        return workflows

    # ══════════════════════════════════════════════════════════════════════════
    #  PREVIEW  –  diff working.json vs original.json
    # ══════════════════════════════════════════════════════════════════════════

    def preview(self) -> str:
        """
        Compare working.json against original.json and return a human-readable
        summary of every change.  Returns the preview text.
        """
        original = self._load_file(self.original_path)
        working = self._load_file(self.working_path)

        orig_map = {str(w["id"]): w for w in original}
        work_map = {str(w["id"]): w for w in working}

        lines: list[str] = []
        changed_ids: list[str] = []

        # ── Changed workflows ──
        for wid, w_wf in work_map.items():
            if wid in orig_map:
                diffs = self._diff_workflow(orig_map[wid], w_wf)
                if diffs:
                    changed_ids.append(wid)
                    name = w_wf.get("name", wid)
                    lines.append(f"\n  CHANGED: {name} (ID: {wid})")
                    for d in diffs:
                        lines.append(f"    {d}")

        # ── Added workflows ──
        added = set(work_map) - set(orig_map)
        for wid in sorted(added):
            name = work_map[wid].get("name", wid)
            lines.append(f"\n  ADDED: {name} (ID: {wid})")

        # ── Removed workflows ──
        removed = set(orig_map) - set(work_map)
        for wid in sorted(removed):
            name = orig_map[wid].get("name", wid)
            lines.append(f"\n  REMOVED: {name} (ID: {wid})")

        if not lines:
            return "No changes detected between original.json and working.json."

        header = (
            f"Preview: {len(changed_ids)} changed, "
            f"{len(added)} added, {len(removed)} removed\n"
        )
        return header + "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════════
    #  UPDATE  –  push changed workflows from working.json → HubSpot
    # ══════════════════════════════════════════════════════════════════════════

    def update(self, dry_run: bool = False) -> list[dict]:
        """
        Push only the changed workflows from working.json to HubSpot.

        Returns a list of {id, name, status, error?} result dicts.
        """
        original = self._load_file(self.original_path)
        working = self._load_file(self.working_path)

        orig_map = {str(w["id"]): w for w in original}
        work_map = {str(w["id"]): w for w in working}

        results = []

        # ── Push changed workflows ──
        for wid, w_wf in work_map.items():
            if wid not in orig_map:
                continue  # skip new workflows for now (use create separately)
            diffs = self._diff_workflow(orig_map[wid], w_wf)
            if not diffs:
                continue

            name = w_wf.get("name", wid)
            if dry_run:
                print(f"  [DRY RUN] Would update: {name} (ID: {wid})")
                results.append({"id": wid, "name": name, "status": "dry_run"})
                continue

            print(f"  Updating: {name} (ID: {wid}) …")
            try:
                # The update requires revisionId and type
                spec = dict(w_wf)
                # Fetch latest revisionId to avoid conflicts
                current = self.client.get_workflow(wid)
                spec["revisionId"] = current.revision_id
                self.client.update_workflow(wid, spec)
                results.append({"id": wid, "name": name, "status": "updated"})
                print(f"    ✓ Updated successfully")
            except Exception as e:
                results.append({"id": wid, "name": name, "status": "error", "error": str(e)})
                print(f"    ✗ Error: {e}")

        if not results:
            print("  No changes to push.")

        # After successful update, refresh original.json to match
        if not dry_run and any(r["status"] == "updated" for r in results):
            print("\n  Refreshing original.json to match current HubSpot state …")
            # Update original with the working copy for pushed items
            for r in results:
                if r["status"] == "updated" and r["id"] in work_map:
                    orig_map[r["id"]] = work_map[r["id"]]
            refreshed = {
                "fetched_at": datetime.now().isoformat(),
                "source": "post-update refresh",
                "count": len(orig_map),
                "workflows": list(orig_map.values()),
            }
            with open(self.original_path, "w") as f:
                json.dump(refreshed, f, indent=2)

        return results

    # ══════════════════════════════════════════════════════════════════════════
    #  LIST  –  quick summary from HubSpot
    # ══════════════════════════════════════════════════════════════════════════

    def list_summaries(self) -> list[WorkflowSummary]:
        """Fetch summary list of all workflows from HubSpot."""
        print("Fetching workflow list …")
        summaries = self.client.list_workflows()
        print(f"  Found {len(summaries)} workflows")
        return summaries

    # ══════════════════════════════════════════════════════════════════════════
    #  MARKDOWN  –  generate docs from working.json
    # ══════════════════════════════════════════════════════════════════════════

    def generate_markdown(self, title: str = "HubSpot Workflows") -> str:
        """Generate markdown documentation from working.json."""
        workflows = self._load_file(self.working_path)
        return self._workflows_to_markdown(workflows, title)

    def save_markdown(self, filename: str = "workflows.md", title: str = "HubSpot Workflows") -> str:
        """Generate and save markdown to the data directory."""
        md = self.generate_markdown(title)
        filepath = os.path.join(self.data_dir, filename)
        with open(filepath, "w") as f:
            f.write(md)
        print(f"  Saved markdown → {filepath}")
        return filepath

    # ══════════════════════════════════════════════════════════════════════════
    #  DISCOVER  –  inspect API fields
    # ══════════════════════════════════════════════════════════════════════════

    def discover_fields(self) -> dict:
        """Fetch one summary + one detail to discover available API fields."""
        summaries = self.client.list_workflows()
        if not summaries:
            return {"error": "No workflows found"}

        sample_summary = summaries[0].raw
        sample_full = self.client.get_workflow(summaries[0].id).raw

        return {
            "summary_fields": sorted(sample_summary.keys()),
            "detail_fields": sorted(sample_full.keys()),
            "summary_sample": sample_summary,
            "detail_sample_name": sample_full.get("name", "N/A"),
            "folder_keys_in_summary": self._detect_folder_keys(sample_summary),
            "folder_keys_in_detail": self._detect_folder_keys(sample_full),
        }

    # ══════════════════════════════════════════════════════════════════════════
    #  STATUS  –  show current state of data files
    # ══════════════════════════════════════════════════════════════════════════

    def status(self) -> str:
        """Return a status summary of the data directory."""
        lines = []

        for label, path in [("original.json", self.original_path), ("working.json", self.working_path)]:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                count = data.get("count", len(data.get("workflows", [])))
                source = data.get("source", "?")
                fetched = data.get("fetched_at", "?")
                size = os.path.getsize(path)
                lines.append(f"  {label:20s}  {count} workflows  from '{source}'  fetched {fetched}  ({size:,} bytes)")
            else:
                lines.append(f"  {label:20s}  (not found – run 'fetch' first)")

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════════
    #  PRIVATE – fetching strategies
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_by_folder_id(self, folder_id: str) -> list[Workflow]:
        """Fetch workflows filtered by HubSpot folder ID."""
        print(f"Fetching workflows in folder ID {folder_id} …")

        # Try passing folderId as query param to the list endpoint
        summaries = self.client.list_workflows(folder_id=folder_id)

        if summaries:
            # Check if the API actually filtered (compare to total count)
            all_summaries = self.client.list_workflows()
            if len(summaries) < len(all_summaries):
                print(f"  API returned {len(summaries)} workflows for folder (vs {len(all_summaries)} total)")
                return self._fetch_details_for(summaries)
            else:
                print(f"  API returned all {len(summaries)} workflows (folderId param may be ignored)")
                # folderId param was ignored – fall back to checking each workflow's detail
                print("  Checking each workflow for folder membership …")
                all_wfs = self._fetch_details_for(summaries)
                matching = [
                    wf for wf in all_wfs
                    if str(wf.raw.get("folderId", "")) == folder_id
                    or str(wf.raw.get("folder_id", "")) == folder_id
                    or str(wf.raw.get("customProperties", {}).get("folderId", "")) == folder_id
                ]
                if matching:
                    print(f"  Found {len(matching)} workflows with folderId={folder_id}")
                    return matching

                # Last resort: API doesn't expose folderId at all
                print(f"  folderId not found in workflow details – returning all {len(all_wfs)} workflows")
                print("  (HubSpot does not expose folder membership via the API)")
                print("  Tip: you can filter by name with --filter instead")
                return all_wfs

        return []

    def _fetch_all_full(self) -> list[Workflow]:
        """Fetch full details for every workflow."""
        summaries = self.list_summaries()
        return self._fetch_details_for(summaries)

    def _fetch_by_name(self, name_filter: str) -> list[Workflow]:
        """Fetch workflows matching a name pattern."""
        summaries = self.list_summaries()
        has_names = any(s.name for s in summaries)

        if has_names:
            matching = [s for s in summaries if name_filter.lower() in s.name.lower()]
        else:
            print("  Summary endpoint did not return names – fetching all for filtering …")
            all_wfs = self._fetch_details_for(summaries)
            return [wf for wf in all_wfs if name_filter.lower() in wf.name.lower()]

        print(f"  {len(matching)} workflows match '{name_filter}'")
        return self._fetch_details_for(matching)

    def _fetch_by_folder(self, folder_name: str) -> list[Workflow]:
        """Fetch workflows from a folder (adaptive strategy)."""
        summaries = self.list_summaries()
        if not summaries:
            return []

        # Check summaries for folder metadata
        folder_keys = self._detect_folder_keys(summaries[0].raw)
        if folder_keys:
            print(f"  Folder metadata in summary: {folder_keys}")
            matching = [s for s in summaries if self._matches_folder(s.raw, folder_keys, folder_name)]
            print(f"  {len(matching)} workflows in folder '{folder_name}'")
            return self._fetch_details_for(matching)

        # Check full detail for folder metadata
        print("  No folder metadata in summaries – checking detail …")
        sample = self.client.get_workflow(summaries[0].id)
        folder_keys = self._detect_folder_keys(sample.raw)
        if folder_keys:
            print(f"  Folder metadata in details: {folder_keys}")
            all_wfs = self._fetch_details_for(summaries)
            return [wf for wf in all_wfs if self._matches_folder(wf.raw, folder_keys, folder_name)]

        # Fall back to name-based matching
        print(f"  No folder metadata in API – falling back to name matching for '{folder_name}'")
        has_names = any(s.name for s in summaries)
        if has_names:
            matching = [s for s in summaries if folder_name.lower() in s.name.lower()]
            if matching:
                print(f"  {len(matching)} workflows match by name")
                return self._fetch_details_for(matching)

        all_wfs = self._fetch_details_for(summaries)
        return [wf for wf in all_wfs if folder_name.lower() in wf.name.lower()]

    def _fetch_details_for(self, summaries: list[WorkflowSummary]) -> list[Workflow]:
        """Fetch full details for each summary."""
        workflows = []
        for i, s in enumerate(summaries, 1):
            print(f"  [{i}/{len(summaries)}] Fetching: {s.name or s.id}")
            try:
                workflows.append(self.client.get_workflow(s.id))
            except Exception as e:
                print(f"    ✗ Error: {e}")
        return workflows

    # ══════════════════════════════════════════════════════════════════════════
    #  PRIVATE – diffing
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _diff_workflow(original: dict, working: dict) -> list[str]:
        """Compare two workflow dicts and return human-readable diff lines."""
        diffs = []
        all_keys = set(original.keys()) | set(working.keys())

        for key in sorted(all_keys):
            if key in _DIFF_IGNORE_KEYS:
                continue
            orig_val = original.get(key)
            work_val = working.get(key)
            if orig_val != work_val:
                if isinstance(orig_val, (dict, list)) or isinstance(work_val, (dict, list)):
                    orig_s = json.dumps(orig_val, sort_keys=True, default=str)
                    work_s = json.dumps(work_val, sort_keys=True, default=str)
                    if orig_s != work_s:
                        # Summarize large diffs
                        if len(orig_s) > 120 or len(work_s) > 120:
                            diffs.append(f"~ {key}: (complex change – review in working.json)")
                        else:
                            diffs.append(f"~ {key}: {orig_s} → {work_s}")
                else:
                    diffs.append(f"~ {key}: {orig_val!r} → {work_val!r}")
        return diffs

    # ══════════════════════════════════════════════════════════════════════════
    #  PRIVATE – folder detection
    # ══════════════════════════════════════════════════════════════════════════

    _FOLDER_KEY_PATTERNS = ("folder", "folderId", "folderName", "folder_id", "folder_name", "parentId")

    @classmethod
    def _detect_folder_keys(cls, raw: dict) -> list[str]:
        found = []
        for key in raw:
            if any(pat.lower() in key.lower() for pat in cls._FOLDER_KEY_PATTERNS):
                found.append(key)
        custom = raw.get("customProperties", {})
        if isinstance(custom, dict):
            for key in custom:
                if any(pat.lower() in key.lower() for pat in cls._FOLDER_KEY_PATTERNS):
                    found.append(f"customProperties.{key}")
        return found

    @staticmethod
    def _matches_folder(raw: dict, folder_keys: list[str], folder_name: str) -> bool:
        target = folder_name.lower()
        for key in folder_keys:
            if key.startswith("customProperties."):
                value = raw.get("customProperties", {}).get(key.split(".", 1)[1], "")
            else:
                value = raw.get(key, "")
            if isinstance(value, str) and target in value.lower():
                return True
            if isinstance(value, (int, float)) and str(value) == folder_name:
                return True
        return False

    # ══════════════════════════════════════════════════════════════════════════
    #  PRIVATE – file I/O
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _load_file(path: str) -> list[dict]:
        """Load workflows list from a JSON envelope file."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}\nRun 'fetch' first.")
        with open(path) as f:
            data = json.load(f)
        return data.get("workflows", data if isinstance(data, list) else [])

    # ══════════════════════════════════════════════════════════════════════════
    #  PRIVATE – markdown generation
    # ══════════════════════════════════════════════════════════════════════════

    def _workflows_to_markdown(self, workflows: list[dict], title: str) -> str:
        lines = [
            f"# {title}",
            "",
            f"*Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}*  ",
            f"*Total workflows: {len(workflows)}*",
            "",
            "---",
            "",
            "## Table of Contents\n",
        ]

        for i, wf in enumerate(workflows, 1):
            name = wf.get("name", f"Workflow {wf.get('id', '?')}")
            anchor = self._slugify(name)
            status = "Enabled" if wf.get("isEnabled") else "Disabled"
            lines.append(f"{i}. [{name}](#{anchor}) — {status}")
        lines.extend(["", "---", ""])

        for wf in workflows:
            lines.extend(self._workflow_to_markdown(wf))
            lines.extend(["---", ""])

        return "\n".join(lines)

    def _workflow_to_markdown(self, wf: dict) -> list[str]:
        name = wf.get("name", f"Workflow {wf.get('id', '?')}")
        lines = [
            f"## {name}",
            "",
            "| Property | Value |",
            "|----------|-------|",
            f"| **ID** | `{wf.get('id', 'N/A')}` |",
            f"| **Status** | {'Enabled' if wf.get('isEnabled') else 'Disabled'} |",
            f"| **Type** | `{wf.get('type', 'N/A')}` |",
            f"| **Object Type** | {OBJECT_TYPE_NAMES.get(wf.get('objectTypeId', ''), wf.get('objectTypeId', 'N/A'))} |",
            f"| **Revision** | {wf.get('revisionId', 'N/A')} |",
            f"| **Created** | {wf.get('createdAt', 'N/A')} |",
            f"| **Updated** | {wf.get('updatedAt', 'N/A')} |",
            "",
        ]

        enrollment = wf.get("enrollmentCriteria", {})
        if enrollment:
            lines.extend(self._enrollment_to_markdown(enrollment))

        actions = wf.get("actions", [])
        if actions:
            lines.extend(self._actions_to_markdown(actions, wf.get("startActionId", "1")))

        suppression = wf.get("suppressionListIds", [])
        if suppression:
            lines.append("### Suppression Lists\n")
            for lid in suppression:
                lines.append(f"- List ID: `{lid}`")
            lines.append("")

        return lines

    def _enrollment_to_markdown(self, enrollment: dict) -> list[str]:
        lines = ["### Enrollment Criteria\n"]
        enrollment_type = enrollment.get("type", "N/A")
        re_enroll = enrollment.get("shouldReEnroll", False)
        lines.append(f"- **Type:** {enrollment_type}")
        lines.append(f"- **Re-enrollment:** {'Yes' if re_enroll else 'No'}")

        if enrollment_type == "EVENT_BASED":
            for branch in enrollment.get("eventFilterBranches", []):
                event_id = branch.get("eventTypeId", "")
                event_name = EVENT_TYPE_NAMES.get(event_id, f"Event `{event_id}`")
                lines.append(f"- **Trigger:** {event_name}")
                for flt in branch.get("filters", []):
                    prop = flt.get("property", "")
                    op = flt.get("operation", {})
                    operator = op.get("operator", "")
                    values = op.get("values", [])
                    lines.append(f"  - Filter: `{prop}` {operator} `{values}`")
        elif enrollment_type == "LIST_BASED":
            list_filter = enrollment.get("listFilterBranch", {})
            for branch in list_filter.get("filterBranches", []):
                for flt in branch.get("filters", []):
                    prop = flt.get("property", "")
                    op = flt.get("operation", {})
                    operator = op.get("operator", "")
                    values = op.get("values", [])
                    lines.append(f"  - Filter: `{prop}` {operator} `{values}`")

        lines.append("")
        return lines

    def _actions_to_markdown(self, actions: list[dict], start_id: str) -> list[str]:
        lines = ["### Actions\n"]
        action_map = {a.get("actionId"): a for a in actions}

        visited: set[str] = set()
        current_id: str | None = start_id
        step = 1

        while current_id and current_id not in visited:
            visited.add(current_id)
            action = action_map.get(current_id)
            if not action:
                break
            lines.extend(self._single_action_md(action, step))
            connection = action.get("connection", {})
            current_id = connection.get("nextActionId")
            step += 1

        remaining = set(action_map.keys()) - visited
        if remaining:
            lines.append("#### Branch Actions\n")
            for aid in sorted(remaining):
                lines.extend(self._single_action_md(action_map[aid], label=f"Branch {aid}"))

        return lines

    def _single_action_md(self, action: dict, step: int | None = None, label: str | None = None) -> list[str]:
        action_type_id = action.get("actionTypeId", "")
        action_name = ACTION_TYPE_NAMES.get(action_type_id, f"Action `{action_type_id}`")
        header = f"**Step {step}: {action_name}**" if step else f"**{label}: {action_name}**"

        lines = [header, ""]

        fields = action.get("fields", {})
        if fields:
            for key, value in fields.items():
                if isinstance(value, (dict, list)):
                    rendered = json.dumps(value, default=str)
                    if len(rendered) > 200:
                        rendered = rendered[:200] + " …"
                    lines.append(f"- `{key}`: `{rendered}`")
                else:
                    lines.append(f"- `{key}`: {value}")

        if action.get("listBranches"):
            lines.append("- *Branching (list-based):*")
            for i, branch in enumerate(action["listBranches"]):
                bname = branch.get("branchName", f"Branch {i + 1}")
                next_id = branch.get("connection", {}).get("nextActionId", "?")
                lines.append(f"  - {bname} → Action {next_id}")
            default_next = action.get("defaultBranch", {}).get("nextActionId", "?")
            lines.append(f"  - *(default)* → Action {default_next}")

        if action.get("staticBranches"):
            lines.append("- *Branching (value-based):*")
            for branch in action["staticBranches"]:
                bval = branch.get("branchValue", "?")
                next_id = branch.get("connection", {}).get("nextActionId", "?")
                lines.append(f"  - `{bval}` → Action {next_id}")
            default_next = action.get("defaultBranch", {}).get("nextActionId", "?")
            lines.append(f"  - *(default)* → Action {default_next}")

        lines.append("")
        return lines

    @staticmethod
    def _slugify(text: str) -> str:
        return (
            text.lower()
            .replace(" ", "-")
            .replace("(", "")
            .replace(")", "")
            .replace("/", "")
            .replace(":", "")
            .replace("'", "")
        )
