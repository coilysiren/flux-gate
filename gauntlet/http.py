"""HTTP execution surface — the only adapter Gauntlet ships with.

Carryover from when the adversarial loop was meant to span multiple surfaces
(HTTP, CLI, WebDriver) is gone. There is one execution mode, and the Drone
calls into ``HttpApi.send`` directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import requests as http

from .models import HttpRequest, HttpResponse

# Filter applied to ``requests.Response.headers`` before surfacing them on
# ``ExecutionStepResult.response_headers``. Keeping this list short and
# module-level makes "what the Inspector gets to see" obvious and auditable.
# Any header starting with ``X-`` also passes (fingerprinting / custom
# framework headers are often the actionable signal).
_INTERESTING_HEADERS = frozenset(
    h.lower()
    for h in (
        "Server",
        "X-Powered-By",
        "Content-Type",
        "Set-Cookie",
        # Standard security headers — omissions + misconfigurations here are
        # the Inspector's bread and butter.
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Frame-Options",
        "X-Content-Type-Options",
        "Referrer-Policy",
    )
)


Outcome = Literal["ok", "timeout", "connection_reset", "dns_failure", "other_error"]


@dataclass
class SendResult:
    """Rich return value from ``HttpApi.send``.

    Carries the ``HttpResponse`` (the shape the rest of Gauntlet already
    speaks) plus transport-level metadata the Drone folds into
    ``ExecutionStepResult``. Kept as a dataclass rather than a Pydantic model
    because it never crosses an MCP or JSONL boundary — it lives only
    between ``HttpApi`` and ``Drone``.
    """

    response: HttpResponse
    duration_ms: float = 0.0
    response_size_bytes: int = 0
    response_headers: dict[str, str] = field(default_factory=dict)
    outcome: Outcome = "ok"


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

    def send(self, user: str, request: HttpRequest) -> SendResult:
        """Send ``request`` as ``user`` and return the response + metadata.

        Never raises on network errors — the Drone needs to record them as
        ``outcome != "ok"`` rather than let the whole plan crash. HTTP errors
        (4xx/5xx) are not network errors and are returned as ``ok`` with the
        underlying ``status_code``.
        """
        headers = {"X-User": user, **self._user_headers.get(user, {})}
        url = f"{self._base_url}{request.path}"

        start = time.perf_counter()
        try:
            resp = http.request(
                request.method,
                url,
                json=request.body if request.body else None,
                headers=headers,
                timeout=10,
            )
        except http.exceptions.Timeout:
            duration_ms = (time.perf_counter() - start) * 1000.0
            return SendResult(
                response=HttpResponse(status_code=0, body={}),
                duration_ms=duration_ms,
                outcome="timeout",
            )
        except http.exceptions.ConnectionError as exc:
            duration_ms = (time.perf_counter() - start) * 1000.0
            return SendResult(
                response=HttpResponse(status_code=0, body={}),
                duration_ms=duration_ms,
                outcome=_classify_connection_error(exc),
            )
        except http.exceptions.RequestException:
            duration_ms = (time.perf_counter() - start) * 1000.0
            return SendResult(
                response=HttpResponse(status_code=0, body={}),
                duration_ms=duration_ms,
                outcome="other_error",
            )

        duration_ms = (time.perf_counter() - start) * 1000.0

        body: dict[str, Any]
        try:
            body = resp.json()
        except ValueError:
            body = {"_raw": resp.text}

        return SendResult(
            response=HttpResponse(status_code=resp.status_code, body=body),
            duration_ms=duration_ms,
            response_size_bytes=len(resp.content),
            response_headers=_filter_headers(resp.headers),
            outcome="ok",
        )


def _filter_headers(headers: Any) -> dict[str, str]:
    """Return only the subset of ``headers`` the Inspector is allowed to see.

    Accepts anything dict-like. Case-insensitive matching against
    ``_INTERESTING_HEADERS`` plus a prefix rule for ``X-*``.
    """
    out: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower in _INTERESTING_HEADERS or lower.startswith("x-"):
            out[name] = value
    return out


def _classify_connection_error(exc: BaseException) -> Outcome:
    """Best-effort classification of a ``requests.ConnectionError``.

    The underlying causes come from urllib3 / socket errors and aren't a
    stable class hierarchy, so we fall back to the exception text. False
    positives are tolerable; the Inspector reads the string value alongside
    the classification.
    """
    text = (repr(exc) + " " + str(exc)).lower()
    if "name or service not known" in text or "nodename nor servname" in text:
        return "dns_failure"
    if "getaddrinfo" in text or "temporary failure in name resolution" in text:
        return "dns_failure"
    if "connection reset" in text or "econnreset" in text:
        return "connection_reset"
    return "other_error"


__all__ = ["HttpApi", "SendResult"]
