#!/usr/bin/env python3
"""
HubSpot Workflow Manager – collaborative fetch → edit → preview → update cycle.

Usage:
    python -m workflows fetch --folder "Standard Labs"    Fetch workflows from a folder
    python -m workflows fetch --filter "onboarding"       Fetch by name pattern
    python -m workflows fetch --all                       Fetch every workflow

    python -m workflows status                            Show state of data files
    python -m workflows preview                           Show what changed vs original
    python -m workflows markdown                          Generate readable docs

    python -m workflows update                            Push changes to HubSpot
    python -m workflows update --dry-run                  Preview what would be pushed

    python -m workflows list                              Quick summary from HubSpot
    python -m workflows show <flow_id>                    Print one workflow as JSON
    python -m workflows discover                          Inspect API response fields

Workflow:
    1. fetch   → pulls from HubSpot → saves original.json + working.json
    2. edit    → we discuss & edit working.json together in chat
    3. preview → shows diff of working.json vs original.json
    4. update  → pushes only changed workflows to HubSpot

Environment:
    HUBSPOT_API_KEY   HubSpot private app access token (required)
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from .client import WorkflowClient
from .manager import WorkflowManager, OBJECT_TYPE_NAMES


def get_manager() -> WorkflowManager:
    """Initialize the workflow manager from environment."""
    load_dotenv()
    api_key = os.environ.get("HUBSPOT_API_KEY")
    if not api_key:
        print("Error: HUBSPOT_API_KEY environment variable is not set.")
        print("Set it in your .env file or export it in your shell.")
        sys.exit(1)

    client = WorkflowClient(api_key)
    return WorkflowManager(client)


# ── Commands ───────────────────────────────────────────────────────────────


def cmd_fetch(args):
    """Fetch workflows from HubSpot → original.json + working.json."""
    manager = get_manager()

    if not (args.folder_id or args.folder or args.filter or args.all):
        print("Specify what to fetch:\n")
        print('  --folder-id 1080257022145  Fetch by HubSpot folder ID')
        print('  --folder "Standard Labs"   Fetch by folder name')
        print('  --filter "onboarding"      Fetch by workflow name')
        print("  --all                      Fetch every workflow")
        return

    workflows = manager.fetch(
        folder_id=args.folder_id,
        folder=args.folder,
        name_filter=args.filter,
        fetch_all=args.all,
    )

    if workflows:
        print(f"\nReady! Edit {manager.working_path} then run:")
        print("  python -m workflows preview   (see your changes)")
        print("  python -m workflows update    (push to HubSpot)")


def cmd_status(args):
    """Show current state of data files."""
    manager = get_manager()
    print("\nData files:\n")
    print(manager.status())
    print()


def cmd_preview(args):
    """Show what changed in working.json vs original.json."""
    manager = get_manager()
    try:
        result = manager.preview()
        print(f"\n{result}\n")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_update(args):
    """Push changed workflows from working.json to HubSpot."""
    manager = get_manager()

    if args.dry_run:
        print("\n[DRY RUN] Showing what would be updated:\n")

    try:
        # Show preview first
        preview = manager.preview()
        print(f"\n{preview}\n")

        if "No changes detected" in preview:
            return

        if not args.dry_run:
            print("Pushing changes to HubSpot …\n")

        results = manager.update(dry_run=args.dry_run)

        # Summary
        updated = sum(1 for r in results if r["status"] == "updated")
        errors = sum(1 for r in results if r["status"] == "error")
        if updated:
            print(f"\n  ✓ {updated} workflow(s) updated successfully")
        if errors:
            print(f"  ✗ {errors} workflow(s) failed")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_list(args):
    """List all workflows with a summary table."""
    manager = get_manager()
    summaries = manager.list_summaries()

    if not summaries:
        print("\nNo workflows found.")
        return

    print(f"\n{'ID':<15} {'Status':<10} {'Object Type':<15} {'Name'}")
    print("─" * 80)
    for s in summaries:
        status = "ON" if s.is_enabled else "OFF"
        obj_type = OBJECT_TYPE_NAMES.get(s.object_type_id, s.object_type_id)
        print(f"{s.id:<15} {status:<10} {obj_type:<15} {s.name}")
    print(f"\nTotal: {len(summaries)} workflows")


def cmd_show(args):
    """Show full JSON for a specific workflow."""
    manager = get_manager()
    print(f"Fetching workflow {args.flow_id} …")
    try:
        wf = manager.client.get_workflow(args.flow_id)
        print(json.dumps(wf.raw, indent=2))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_markdown(args):
    """Generate Markdown documentation from working.json."""
    manager = get_manager()
    try:
        title = args.title or "HubSpot Workflows"
        filepath = manager.save_markdown(title=title)
        print(f"\nDone! Markdown saved to {filepath}")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_discover(args):
    """Inspect API response fields (debug folder support)."""
    manager = get_manager()

    print("Inspecting API response fields …\n")
    try:
        info = manager.discover_fields()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("── Summary endpoint fields ──")
    for key in info["summary_fields"]:
        val = info["summary_sample"].get(key, "")
        preview = str(val)[:80] if not isinstance(val, (dict, list)) else f"({type(val).__name__})"
        print(f"  {key:30s} {preview}")

    print("\n── Detail endpoint fields ──")
    for key in info["detail_fields"]:
        print(f"  {key}")

    print("\n── Folder detection ──")
    folder_s = info["folder_keys_in_summary"]
    folder_d = info["folder_keys_in_detail"]
    print(f"  In summary: {folder_s or '(none found)'}")
    print(f"  In detail:  {folder_d or '(none found)'}")

    if not folder_s and not folder_d:
        print("\n  HubSpot may not expose folder metadata via the API.")
        print("  The --folder flag will fall back to name-based matching.")

    print(f"\n── Sample workflow ──")
    print(f"  Name: {info.get('detail_sample_name', 'N/A')}")
    print("\nFull summary sample:")
    print(json.dumps(info["summary_sample"], indent=2))


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="HubSpot Workflow Manager – fetch, edit, preview, update.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # fetch
    fp = sub.add_parser("fetch", help="Fetch workflows → original.json + working.json")
    fp.add_argument("--folder-id", type=str, help="Filter by HubSpot folder ID")
    fp.add_argument("--folder", type=str, help="Filter by HubSpot folder name")
    fp.add_argument("--filter", type=str, help="Filter by workflow name")
    fp.add_argument("--all", action="store_true", help="Fetch all workflows")

    # status
    sub.add_parser("status", help="Show state of data files")

    # preview
    sub.add_parser("preview", help="Diff working.json vs original.json")

    # update
    up = sub.add_parser("update", help="Push changes to HubSpot")
    up.add_argument("--dry-run", action="store_true", help="Preview only, don't push")

    # markdown
    mp = sub.add_parser("markdown", help="Generate docs from working.json")
    mp.add_argument("--title", type=str, help="Document title")

    # list
    sub.add_parser("list", help="Quick summary from HubSpot")

    # show
    sp = sub.add_parser("show", help="Show one workflow as JSON")
    sp.add_argument("flow_id", type=str, help="Workflow flow ID")

    # discover
    sub.add_parser("discover", help="Inspect API response fields")

    args = parser.parse_args()

    commands = {
        "fetch": cmd_fetch,
        "status": cmd_status,
        "preview": cmd_preview,
        "update": cmd_update,
        "markdown": cmd_markdown,
        "list": cmd_list,
        "show": cmd_show,
        "discover": cmd_discover,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
