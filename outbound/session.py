"""Session management for outbound lead generation.

Each session lives in outbound/sessions/session-NNN/ and contains:
    session.json          - Metadata: method, prompt, status, timestamps
    discovery.csv         - Raw leads: name, domain, source, additional_info
    interesting_finds.md  - Unstructured agent observations
    qualified.csv         - After qualification (future)
    enriched.csv          - After enrichment (future)
    agent_log.json        - Full agent conversation for debugging
"""

import csv
import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

SESSIONS_DIR = Path(__file__).parent / "sessions"


@dataclass
class Session:
    """An outbound lead generation session."""

    id: str
    created_at: str
    method: str  # search | browse | list
    prompt: str
    status: str = "discovery"  # discovery | qualification | enrichment | pushed
    discovery_runs: int = 0

    # --- Paths ---

    @property
    def dir(self) -> Path:
        return SESSIONS_DIR / self.id

    @property
    def discovery_csv(self) -> Path:
        return self.dir / "discovery.csv"

    @property
    def interesting_finds_md(self) -> Path:
        return self.dir / "interesting_finds.md"

    @property
    def qualified_csv(self) -> Path:
        return self.dir / "qualified.csv"

    @property
    def enriched_csv(self) -> Path:
        return self.dir / "enriched.csv"

    @property
    def agent_log(self) -> Path:
        return self.dir / "agent_log.json"

    # --- Persistence ---

    def save(self):
        """Save session metadata to session.json."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "session.json", "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, session_id: str) -> "Session":
        """Load a session from its directory."""
        session_file = SESSIONS_DIR / session_id / "session.json"
        if not session_file.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        with open(session_file) as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def create(cls, method: str, prompt: str) -> "Session":
        """Create a new session with the next available ID."""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        # Find next session number
        existing = sorted(SESSIONS_DIR.glob("session-*"))
        next_num = 1
        if existing:
            for d in reversed(existing):
                try:
                    next_num = int(d.name.split("-")[1]) + 1
                    break
                except (IndexError, ValueError):
                    continue

        session = cls(
            id=f"session-{next_num:03d}",
            created_at=datetime.now(timezone.utc).isoformat(),
            method=method,
            prompt=prompt,
        )
        session.save()
        session._init_discovery_csv()
        session._init_interesting_finds()

        return session

    # --- Discovery CSV ---

    def _init_discovery_csv(self):
        """Create empty discovery CSV with headers."""
        with open(self.discovery_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "domain", "source", "additional_info"])

    def _init_interesting_finds(self):
        """Create the interesting finds markdown file."""
        with open(self.interesting_finds_md, "w") as f:
            f.write(f"# Interesting Finds\n\n")
            f.write(f"**Session:** {self.id}  \n")
            f.write(f"**Prompt:** {self.prompt}  \n\n")
            f.write("---\n\n")

    def add_lead(
        self,
        name: str,
        domain: str,
        source: str = "",
        additional_info: str = "",
    ) -> int:
        """Append a lead to discovery.csv. Returns updated lead count."""
        # Check for duplicate domain
        existing_domains = {l["domain"].lower().strip() for l in self.leads}
        domain_clean = domain.lower().strip()
        if domain_clean in existing_domains:
            return self.lead_count  # Skip duplicate

        with open(self.discovery_csv, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([name.strip(), domain_clean, source, additional_info.strip()])
        return self.lead_count

    def add_finding(self, note: str):
        """Append a note to interesting_finds.md."""
        with open(self.interesting_finds_md, "a") as f:
            timestamp = datetime.now().strftime("%H:%M")
            f.write(f"- [{timestamp}] {note}\n\n")

    @property
    def lead_count(self) -> int:
        """Count leads in discovery.csv (excluding header)."""
        if not self.discovery_csv.exists():
            return 0
        with open(self.discovery_csv) as f:
            return max(0, sum(1 for _ in csv.reader(f)) - 1)

    @property
    def leads(self) -> list[dict]:
        """Read all leads from discovery.csv as dicts."""
        if not self.discovery_csv.exists():
            return []
        with open(self.discovery_csv) as f:
            return list(csv.DictReader(f))

    # --- Listing ---

    @classmethod
    def list_all(cls) -> list["Session"]:
        """List all sessions, sorted by ID."""
        sessions = []
        for session_dir in sorted(SESSIONS_DIR.glob("session-*")):
            try:
                sessions.append(cls.load(session_dir.name))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
        return sessions
