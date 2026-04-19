from __future__ import annotations

from typing import Any

import requests as http

from ..models import Action, HttpRequest, HttpResponse, Observation


class HttpApi:
    """Sends real HTTP requests to a locally-running API process.

    Each user is identified by an ``X-User`` header by default. Pass
    ``user_headers`` to override with bearer tokens or session cookies.

    Example::

        adapter = HttpApi(
            "http://localhost:8000",
            user_headers={
                "userA": {"Authorization": "Bearer token-a"},
                "userB": {"Authorization": "Bearer token-b"},
            },
        )
    """

    def __init__(
        self,
        base_url: str,
        user_headers: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_headers = user_headers or {}

    def send(self, user: str, request: HttpRequest) -> HttpResponse:
        headers = {"X-User": user, **self._user_headers.get(user, {})}
        resp = http.request(
            request.method,
            f"{self._base_url}{request.path}",
            json=request.body if request.body else None,
            headers=headers,
            timeout=10,
        )
        body: dict[str, Any]
        try:
            body = resp.json()
        except ValueError:
            body = {"_raw": resp.text}
        return HttpResponse(status_code=resp.status_code, body=body)

    def execute(self, user: str, action: Action) -> Observation:
        response = self.send(user, action.to_http_request())
        return Observation.from_http_response(response)
