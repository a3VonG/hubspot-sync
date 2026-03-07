#!/usr/bin/env python3
"""Standalone callback server with ngrok tunnel.

Starts a local HTTP server and exposes it via ngrok so Clay (or anything
else) can POST results back. Every incoming POST is logged with its path
and parsed JSON body.

Run this, copy the ngrok URL, and use it as callback_url when testing
Clay webhooks manually.

Usage:
    python enrich/callback_server.py            # default port 8742
    python enrich/callback_server.py 9000       # custom port
"""

import json
import os
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

print = partial(print, flush=True)

import ngrok
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8742


class CallbackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"raw": body.decode(errors="replace")}

        print(f"\n--- POST {self.path} ---")
        print(json.dumps(data, indent=2))

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def ngrok_is_running(port: int) -> bool:
    try:
        resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        tunnels = [t["config"]["addr"] for t in resp.json()["tunnels"]]
        return f"http://localhost:{port}" in tunnels
    except Exception:
        return False


def get_existing_ngrok_url(port: int) -> str:
    resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
    tunnels = resp.json()["tunnels"]
    return next(
        t["public_url"] for t in tunnels
        if t["config"]["addr"] == f"http://localhost:{port}"
    )


def start_ngrok(port: int) -> str:
    listener = ngrok.forward(port, authtoken_from_env=True)
    return listener.url()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), CallbackHandler)
    print(f"Callback server listening on port {PORT}")

    if ngrok_is_running(PORT):
        ngrok_url = get_existing_ngrok_url(PORT)
        print(f"Existing ngrok tunnel: {ngrok_url}")
    else:
        ngrok_url = start_ngrok(PORT)
        print(f"ngrok tunnel: {ngrok_url}")

    print(f"\nUse as callback_url: {ngrok_url}/your-path-here")
    print("Waiting for POSTs... (Ctrl+C to stop)\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
