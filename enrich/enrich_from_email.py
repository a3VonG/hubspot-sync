#!/usr/bin/env python3
"""Batch-check domains against Clay's "is dental lab" webhook.

Spins up one callback server + ngrok tunnel, fires off all Clay requests
without waiting, then collects callbacks as they arrive (in any order).

Requires CLAY_URL_IS_DENTAL_LAB and NGROK_AUTHTOKEN in .env.

Usage:
    python enrich/enrich_from_email.py                                  # default test domains
    python enrich/enrich_from_email.py https://a.com https://b.com ...  # custom list
"""

import json
import os
import sys
import threading
import urllib.parse
from dataclasses import dataclass
from functools import partial
from http.server import HTTPServer
from pathlib import Path

print = partial(print, flush=True)

import requests
from dotenv import load_dotenv

from enrich.callback_server import (
    PORT,
    CallbackHandler,
    get_existing_ngrok_url,
    ngrok_is_running,
    start_ngrok,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

CLAY_URL = os.environ.get("CLAY_URL_IS_DENTAL_LAB")
if not CLAY_URL:
    print("ERROR: CLAY_URL_IS_DENTAL_LAB not set in .env")
    sys.exit(1)

DEFAULT_DOMAINS = [
    "https://www.excent.eu/",
    "https://orthogem.fr/",
    "https://www.dentaloralsurgery.com/"
]


@dataclass
class DentalLabResult:
    is_dental_lab: bool
    lab_name: str
    domain: str

    @classmethod
    def from_callback(cls, data: dict, domain: str) -> "DentalLabResult":
        return cls(
            is_dental_lab=bool(data.get("is_dental_lab", False)),
            lab_name=data.get("lab_name", ""),
            domain=domain,
        )


class ResultCollector:
    """Thread-safe collector that tracks pending domains and stores results."""

    def __init__(self, domains: list[str]):
        self._lock = threading.Lock()
        self._pending = {self._key(d) for d in domains}
        self._results: dict[str, dict] = {}
        self._all_done = threading.Event()

    @staticmethod
    def _key(domain: str) -> str:
        return urllib.parse.quote(domain, safe="")

    def receive(self, domain_key: str, data: dict):
        with self._lock:
            self._results[domain_key] = data
            self._pending.discard(domain_key)
            remaining = len(self._pending)
        print(f"  [{len(self._results)} received, {remaining} pending]")
        if remaining == 0:
            self._all_done.set()

    def wait(self, timeout: float) -> bool:
        return self._all_done.wait(timeout=timeout)

    @property
    def results(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._results)

    @property
    def pending(self) -> set[str]:
        with self._lock:
            return set(self._pending)


collector: ResultCollector | None = None


class EnrichCallbackHandler(CallbackHandler):
    """Stores each callback in the collector and logs it."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"raw": body.decode(errors="replace")}

        domain_key = urllib.parse.unquote(self.path.lstrip("/"))

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

        print(f"\n--- Callback: {domain_key} ---")
        print(json.dumps(data, indent=2))

        if collector is not None:
            collector.receive(urllib.parse.quote(domain_key, safe=""), data)


def submit_domain(domain: str, ngrok_url: str) -> None:
    domain_key = urllib.parse.quote(domain, safe="")
    callback_url = f"{ngrok_url}/{domain_key}"
    payload = {"domain": domain, "callback_url": callback_url}
    resp = requests.post(CLAY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    print(f"  Submitted {domain} -> {resp.status_code}")


if __name__ == "__main__":
    domains = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_DOMAINS

    collector = ResultCollector(domains)

    server = HTTPServer(("0.0.0.0", PORT), EnrichCallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Callback server listening on port {PORT}")

    if ngrok_is_running(PORT):
        ngrok_url = get_existing_ngrok_url(PORT)
        print(f"Existing ngrok tunnel: {ngrok_url}")
    else:
        ngrok_url = start_ngrok(PORT)
        print(f"ngrok tunnel: {ngrok_url}")

    # Fire all requests without waiting for callbacks
    print(f"\nSubmitting {len(domains)} domain(s)...")
    for domain in domains:
        submit_domain(domain, ngrok_url)
    print("All submitted.\n")

    timeout_sec = 500
    print(f"Waiting up to {timeout_sec}s for all callbacks...")
    all_arrived = collector.wait(timeout=timeout_sec)

    # Print results for everything we got back
    print(f"\n{'=' * 50}")
    print(f"Results: {len(collector.results)}/{len(domains)}")
    print(f"{'=' * 50}")
    for domain in domains:
        key = urllib.parse.quote(domain, safe="")
        raw = collector.results.get(key)
        if raw is None:
            print(f"\n  {domain}: NO CALLBACK RECEIVED")
            continue
        result = DentalLabResult.from_callback(raw, domain)
        print(f"\n  {result.domain}")
        print(f"    is_dental_lab: {result.is_dental_lab}")
        print(f"    lab_name:      {result.lab_name}")

    if not all_arrived:
        pending = collector.pending
        print(f"\nTimed out. Still pending: {[urllib.parse.unquote(p) for p in pending]}")
        sys.exit(1)

    server.shutdown()
