# Outbound Lead Generation - Agent Prompt

Read this file at the start of every session involving outbound lead generation.

## What this module does

The `outbound/` package is a session-based, agent-driven pipeline for finding, qualifying, and enriching dental lab leads, then importing them into HubSpot.

Each session progresses through stages:

```
Discovery  →  Qualification  →  Enrichment  →  HubSpot Push
(find labs)    (verify & classify)  (details + contacts)  (import)
```

Between each stage, results are saved as CSV files that can be reviewed and edited manually.

## Quick start

```bash
# Always activate venv first
source venv/bin/activate

# Create a session
python -m outbound new "Find orthodontic laboratories in Italy" --method search

# Run discovery
python -m outbound discover session-001

# Check status
python -m outbound status session-001

# Run discovery again to find more (agent sees existing leads, avoids dupes)
python -m outbound discover session-001

# List all sessions
python -m outbound status
```

## Session structure

```
outbound/sessions/session-001/
    session.json          ← Metadata: method, prompt, status
    discovery.csv         ← Raw leads: name, domain, source, additional_info
    interesting_finds.md  ← Unstructured agent observations for sales team
    qualified.csv         ← After qualification (future)
    enriched.csv          ← After enrichment (future)
    agent_log.json        ← Full agent conversation for debugging
```

## Discovery methods

### A) Search (`--method search`)

Agent uses Google Custom Search to find dental labs based on a description.

```bash
python -m outbound new "Find dental laboratories in Germany" --method search
python -m outbound new "Find CAD/CAM milling centers in Scandinavia" --method search
python -m outbound new "Find companies similar to DentalWings" --method search
```

The agent will:
- Generate varied search queries (different keywords, languages, locations)
- Browse results to verify they're dental labs
- Search for directories and trade associations
- Save every plausible lead

### B) Browse (`--method browse`)

Agent browses a specific URL (directory, list, association page) and extracts labs.

```bash
python -m outbound new "https://example.com/dental-lab-directory" --method browse
```

### C) List enrichment (`--method list`)

Agent takes a list of names/domains and fills in missing information.

```bash
python -m outbound new "Acme Dental Lab, dentalworks.it, ProDent GmbH" --method list
```

## Pipeline stages

### Stage 1: Discovery (implemented)

**Goal:** Build a rough list of potential leads with at minimum a name and domain.

**Output:** `discovery.csv` (name, domain, source, additional_info) + `interesting_finds.md`

**Iterative:** Run `discover` multiple times on the same session. The agent sees existing leads and searches for new ones. Edit the CSV between runs if needed.

### Stage 2: Qualification (planned)

**Goal:** Verify each lead is actually a dental lab, classify it, check HubSpot for duplicates.

**Output:** `qualified.csv` (+ is_dental_lab, lab_type, description, in_hubspot)

**Will use:** Website browsing, HubSpot API (existing `clients/hubspot.py`), optionally Clay.

### Stage 3: Enrichment (planned)

**Goal:** Gather detailed company data and find contacts.

**Output:** `enriched.csv` (+ size, revenue, country, address, linkedin, contacts)

**Will use:** Website browsing, Google search, Clay batch enrichment, LinkedIn.

### Stage 4: HubSpot Push (planned)

**Goal:** Create companies and contacts in HubSpot.

**Uses:** Existing `clients/hubspot.py` client. Deterministic, not agent-driven.

## Architecture

### Agent loop (`agent.py`)

A simple tool-calling loop using the Anthropic SDK directly (~80 lines). No framework.

```
User message → Claude → Tool calls? → Execute tools → Feed results back → Repeat
                                   ↘ No tools → Done
```

### Tools

| Tool | Module | Used in |
|------|--------|---------|
| `google_search` | `tools/google_search.py` | Discovery |
| `browse_website` | `tools/web_browse.py` | All stages |
| `save_lead` | Session-bound (in `__main__.py`) | Discovery |
| `note_finding` | Session-bound (in `__main__.py`) | All stages |
| `check_hubspot` | Planned | Qualification |
| `qualify_lead` | Planned | Qualification |
| `enrich_lead` | Planned | Enrichment |

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `GOOGLE_API_KEY` | For search method | Google API key |
| `GOOGLE_CX` | For search method | Google Custom Search Engine ID |
| `OUTBOUND_MODEL` | No | Override model (default: claude-sonnet-4-20250514) |
| `HUBSPOT_API_KEY` | For qualification/push | Already configured for sync |

### Design principles

1. **CSVs as checkpoints.** Human-readable, editable, inspectable between stages.
2. **One agent loop for everything.** Same loop, different prompts and tools per stage.
3. **No framework.** Direct Anthropic SDK. Nothing hidden, easy to debug.
4. **Session = directory.** All state lives in files. No database, no queue.
5. **Iterative discovery.** Run discover multiple times, agent builds on previous results.
6. **Reuse existing code.** `clients/hubspot.py` for HubSpot, `config.py` for env vars.
