from __future__ import annotations

from copy import deepcopy
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


class InMemoryHttpApi:
    """Demo REST API with an intentional authorization flaw."""

    def __init__(self) -> None:
        self._tasks: dict[int, dict[str, object]] = {}
        self._next_id = 1

    def send(self, user: str, request: HttpRequest) -> HttpResponse:
        if request.method == "POST" and request.path == "/tasks":
            return self._create_task(user, request)
        if request.method == "GET" and request.path.startswith("/tasks/"):
            return self._get_task(user, request)
        if request.method == "PATCH" and request.path.startswith("/tasks/"):
            return self._patch_task(user, request)
        return HttpResponse(status_code=404, body={"error": "not_found"})

    def execute(self, user: str, action: Action) -> Observation:
        response = self.send(user, action.to_http_request())
        return Observation.from_http_response(response)

    def _create_task(self, user: str, request: HttpRequest) -> HttpResponse:
        task_id = self._next_id
        self._next_id += 1
        task = {
            "id": task_id,
            "owner": user,
            "title": request.body.get("title", ""),
            "completed": bool(request.body.get("completed", False)),
        }
        self._tasks[task_id] = task
        return HttpResponse(status_code=201, body=deepcopy(task))

    def _get_task(self, user: str, request: HttpRequest) -> HttpResponse:
        task = self._tasks.get(_task_id_from_path(request.path))
        if task is None:
            return HttpResponse(status_code=404, body={"error": "not_found"})
        if task["owner"] != user:
            return HttpResponse(status_code=403, body={"error": "forbidden"})
        return HttpResponse(status_code=200, body=deepcopy(task))

    def _patch_task(self, user: str, request: HttpRequest) -> HttpResponse:
        task = self._tasks.get(_task_id_from_path(request.path))
        if task is None:
            return HttpResponse(status_code=404, body={"error": "not_found"})

        # This is the seeded flaw that the adversarial loop should surface.
        task["title"] = request.body.get("title", task["title"])
        task["completed"] = request.body.get("completed", task["completed"])
        task["last_modified_by"] = user
        return HttpResponse(status_code=200, body=deepcopy(task))


def _task_id_from_path(path: str) -> int:
    return int(path.rsplit("/", maxsplit=1)[-1])
