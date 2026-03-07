"""CLI entry point: python -m outbound <command>

Commands:
    new       Create a new lead generation session
    discover  Run the discovery agent for a session
    qualify   Run qualification (coming soon)
    enrich    Run enrichment (coming soon)
    push      Push to HubSpot (coming soon)
    status    Show session status
"""

import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from outbound.session import Session
from outbound.agent import run_agent
from outbound import prompts
from outbound.tools import google_search, web_browse


# ---------------------------------------------------------------------------
# Tool schemas for session-bound tools (not in tools/ because they need a session)
# ---------------------------------------------------------------------------

SAVE_LEAD_SCHEMA = {
    "name": "save_lead",
    "description": (
        "Save a discovered dental lab to the leads list. Call this for every "
        "company that might be a dental lab. Requires at minimum a name and domain. "
        "Duplicates (same domain) are automatically skipped."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Company name",
            },
            "domain": {
                "type": "string",
                "description": "Company website domain (e.g. example.com)",
            },
            "additional_info": {
                "type": "string",
                "description": "Location, services, lab type, size, or any useful notes",
            },
        },
        "required": ["name", "domain"],
    },
}

NOTE_FINDING_SCHEMA = {
    "name": "note_finding",
    "description": (
        "Record an interesting observation for the sales team. "
        "Examples: 'This is a large lab group with locations in 3 countries', "
        "'Found a dental lab directory at <url>', market observations, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "The observation to record",
            }
        },
        "required": ["note"],
    },
}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_new(args):
    """Create a new session."""
    session = Session.create(method=args.method, prompt=args.prompt)
    print(f"Created {session.id}")
    print(f"  Method:    {session.method}")
    print(f"  Prompt:    {session.prompt}")
    print(f"  Directory: {session.dir}")
    print(f"\nNext: python -m outbound discover {session.id}")


def cmd_discover(args):
    """Run the discovery agent."""
    session = Session.load(args.session_id)

    # Select system prompt by method
    system_prompt = {
        "search": prompts.DISCOVERY_SEARCH,
        "browse": prompts.DISCOVERY_BROWSE,
        "list": prompts.DISCOVERY_LIST,
    }[session.method]

    # Tool schemas
    tools = [
        google_search.SCHEMA,
        web_browse.SCHEMA,
        SAVE_LEAD_SCHEMA,
        NOTE_FINDING_SCHEMA,
    ]

    # Tool executor with session context
    def execute_tool(name: str, tool_input: dict) -> str:
        if name == "google_search":
            return google_search.execute(**tool_input)
        elif name == "browse_website":
            return web_browse.execute(**tool_input)
        elif name == "save_lead":
            count = session.add_lead(
                name=tool_input["name"],
                domain=tool_input["domain"],
                source=session.method,
                additional_info=tool_input.get("additional_info", ""),
            )
            return f"Lead saved. Total leads: {count}"
        elif name == "note_finding":
            session.add_finding(tool_input["note"])
            return "Finding noted."
        else:
            return f"Unknown tool: {name}"

    # Build user message
    existing_count = session.lead_count
    user_message = session.prompt

    if existing_count > 0:
        existing_leads = session.leads
        summary = "\n".join(
            f"- {l['name']} ({l['domain']})" for l in existing_leads
        )
        user_message += (
            f"\n\nAlready discovered ({existing_count} leads):\n{summary}"
            f"\n\nFind MORE leads beyond these. Do not duplicate them."
        )

    # Header
    print(f"{'=' * 60}")
    print(f"Discovery: {session.id} (method: {session.method})")
    if existing_count > 0:
        print(f"Existing leads: {existing_count}")
    print(f"Prompt: {session.prompt}")
    print(f"{'=' * 60}")

    # Run
    messages = run_agent(system_prompt, user_message, tools, execute_tool)

    # Update session
    session.discovery_runs += 1
    session.save()

    # Save full agent conversation log
    with open(session.agent_log, "w") as f:
        json.dump(messages, f, indent=2, default=str)

    # Summary
    new_leads = session.lead_count - existing_count
    print(f"\n{'=' * 60}")
    print(f"Discovery complete.")
    print(f"  New leads:   {new_leads}")
    print(f"  Total leads: {session.lead_count}")
    print(f"  CSV:         {session.discovery_csv}")
    print(f"  Notes:       {session.interesting_finds_md}")
    print(f"  Agent log:   {session.agent_log}")


def cmd_status(args):
    """Show session status."""
    if args.session_id:
        session = Session.load(args.session_id)
        print(f"Session: {session.id}")
        print(f"  Created:         {session.created_at}")
        print(f"  Method:          {session.method}")
        print(f"  Prompt:          {session.prompt}")
        print(f"  Status:          {session.status}")
        print(f"  Discovery runs:  {session.discovery_runs}")
        print(f"  Leads:           {session.lead_count}")
        if session.leads:
            print()
            for lead in session.leads:
                info = f" - {lead['additional_info']}" if lead.get("additional_info") else ""
                print(f"    {lead['name']:30s}  {lead['domain']}{info}")
    else:
        sessions = Session.list_all()
        if not sessions:
            print("No sessions yet.")
            print('Create one: python -m outbound new "your prompt" --method search')
            return
        print(f"{'ID':<15} {'Method':<8} {'Status':<14} {'Leads':<6} Prompt")
        print("-" * 80)
        for s in sessions:
            prompt_short = s.prompt[:38] + ".." if len(s.prompt) > 40 else s.prompt
            print(
                f"{s.id:<15} {s.method:<8} {s.status:<14} {s.lead_count:<6} {prompt_short}"
            )


def cmd_qualify(args):
    """Run qualification (placeholder)."""
    session = Session.load(args.session_id)
    print(f"Qualification for {session.id}: not yet implemented.")
    print(f"  Will: browse each lead, classify dental lab type, check HubSpot for dupes.")


def cmd_enrich(args):
    """Run enrichment (placeholder)."""
    session = Session.load(args.session_id)
    print(f"Enrichment for {session.id}: not yet implemented.")
    print(f"  Will: gather company size, contacts, LinkedIn, etc.")


def cmd_push(args):
    """Push to HubSpot (placeholder)."""
    session = Session.load(args.session_id)
    dry = " (dry run)" if args.dry_run else ""
    print(f"HubSpot push for {session.id}{dry}: not yet implemented.")
    print(f"  Will: create companies and contacts in HubSpot using clients/hubspot.py.")


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="outbound",
        description="Agent-driven outbound lead generation for dental labs",
    )
    sub = parser.add_subparsers(dest="command")

    # new
    p = sub.add_parser("new", help="Create a new session")
    p.add_argument("prompt", help="Search description, URL to browse, or CSV path")
    p.add_argument(
        "--method",
        choices=["search", "browse", "list"],
        default="search",
        help="Discovery method (default: search)",
    )
    p.set_defaults(func=cmd_new)

    # discover
    p = sub.add_parser("discover", help="Run discovery agent")
    p.add_argument("session_id", help="Session ID (e.g. session-001)")
    p.set_defaults(func=cmd_discover)

    # qualify
    p = sub.add_parser("qualify", help="Run qualification")
    p.add_argument("session_id")
    p.set_defaults(func=cmd_qualify)

    # enrich
    p = sub.add_parser("enrich", help="Run enrichment")
    p.add_argument("session_id")
    p.set_defaults(func=cmd_enrich)

    # push
    p = sub.add_parser("push", help="Push to HubSpot")
    p.add_argument("session_id")
    p.add_argument("--dry-run", action="store_true", help="Preview without pushing")
    p.set_defaults(func=cmd_push)

    # status
    p = sub.add_parser("status", help="Show session status")
    p.add_argument("session_id", nargs="?", help="Session ID (omit to list all)")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
