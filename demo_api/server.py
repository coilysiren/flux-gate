"""Minimal HTTP server that exposes InMemoryHttpApi over the wire.

Intended only as a demo target for `gauntlet`.  Not for production use.

Usage::

    uv run python demo_api/server.py          # port 8000
    uv run python demo_api/server.py 9000     # custom port
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from gauntlet import HttpRequest, InMemoryHttpApi
from gauntlet.models import HttpResponse

_api = InMemoryHttpApi()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def _handle(self, method: str) -> None:
        if self.path == "/health":
            self._respond(HttpResponse(status_code=200, body={"status": "ok"}))
            return

        user = self.headers.get("X-User", "anonymous")
        length = int(self.headers.get("Content-Length", 0))
        body: dict[str, Any] = json.loads(self.rfile.read(length)) if length else {}

        request = HttpRequest(method=method, path=self.path, body=body)  # type: ignore[arg-type]
        response = _api.send(user, request)
        self._respond(response)

    def _respond(self, response: HttpResponse) -> None:
        self.send_response(response.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response.body).encode())

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        print(f"{self.address_string()} {format % args}", flush=True)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = HTTPServer(("0.0.0.0", port), _Handler)
    print(f"demo api listening on http://0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
