"""``HttpApi.send`` — metadata capture and network-error classification."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from gauntlet import HttpRequest
from gauntlet.http import HttpApi, _classify_connection_error, _filter_headers


class _FakeResponse:
    """Stand-in for ``requests.Response`` with just the attributes ``send`` reads."""

    def __init__(
        self,
        status_code: int,
        body_json: Any,
        content: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body_json = body_json
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.headers = headers or {}

    def json(self) -> Any:
        if self._body_json is _RAISE:
            raise ValueError("not json")
        return self._body_json


_RAISE = object()


def test_send_populates_duration_size_headers_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean 200 response populates all metadata fields."""

    def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            status_code=200,
            body_json={"ok": True},
            content=b'{"ok":true}',
            headers={
                "Server": "TestServer/1.0",
                "Content-Type": "application/json",
                "X-Custom-Header": "hello",
                "Date": "Tue, 19 Apr 2026 00:00:00 GMT",  # should be filtered out
            },
        )

    monkeypatch.setattr("gauntlet.http.http.request", fake_request)

    api = HttpApi("http://unused")
    result = api.send("userA", HttpRequest(method="GET", path="/x"))

    assert result.outcome == "ok"
    assert result.response.status_code == 200
    assert result.response.body == {"ok": True}
    assert result.response_size_bytes == len(b'{"ok":true}')
    assert result.duration_ms >= 0.0
    assert result.response_headers == {
        "Server": "TestServer/1.0",
        "Content-Type": "application/json",
        "X-Custom-Header": "hello",
    }
    # "Date" is not on the allowlist and not X-prefixed.
    assert "Date" not in result.response_headers


def test_send_classifies_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raised ``requests.Timeout`` surfaces as ``outcome='timeout'``."""

    def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        raise requests.exceptions.Timeout("request timed out")

    monkeypatch.setattr("gauntlet.http.http.request", fake_request)

    api = HttpApi("http://unused")
    result = api.send("userA", HttpRequest(method="GET", path="/slow"))

    assert result.outcome == "timeout"
    assert result.response.status_code == 0
    assert result.response.body == {}
    assert result.duration_ms >= 0.0


def test_send_classifies_connection_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection reset surfaces as ``outcome='connection_reset'``."""

    def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        raise requests.exceptions.ConnectionError("Connection reset by peer")

    monkeypatch.setattr("gauntlet.http.http.request", fake_request)

    api = HttpApi("http://unused")
    result = api.send("userA", HttpRequest(method="GET", path="/x"))

    assert result.outcome == "connection_reset"


def test_send_classifies_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DNS failure surfaces as ``outcome='dns_failure'``."""

    def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        raise requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='nope.invalid', port=443): Name or service not known"
        )

    monkeypatch.setattr("gauntlet.http.http.request", fake_request)

    api = HttpApi("http://unused")
    result = api.send("userA", HttpRequest(method="GET", path="/x"))

    assert result.outcome == "dns_failure"


def test_send_falls_back_to_other_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrecognized ``RequestException`` subclasses surface as ``other_error``."""

    def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        raise requests.exceptions.InvalidURL("bad URL")

    monkeypatch.setattr("gauntlet.http.http.request", fake_request)

    api = HttpApi("http://unused")
    result = api.send("userA", HttpRequest(method="GET", path="/x"))

    assert result.outcome == "other_error"


def test_send_tolerates_non_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-JSON response body surfaces as ``{"_raw": ...}`` with correct size."""

    def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            status_code=500,
            body_json=_RAISE,
            content=b"<html>server error</html>",
        )

    monkeypatch.setattr("gauntlet.http.http.request", fake_request)

    api = HttpApi("http://unused")
    result = api.send("userA", HttpRequest(method="GET", path="/x"))

    assert result.outcome == "ok"
    assert result.response.status_code == 500
    assert result.response.body == {"_raw": "<html>server error</html>"}
    assert result.response_size_bytes == len(b"<html>server error</html>")


# ---------------------------------------------------------------------------
# Helper-function coverage
# ---------------------------------------------------------------------------


def test_filter_headers_keeps_allowlist_and_x_prefix() -> None:
    filtered = _filter_headers(
        {
            "Server": "nginx",
            "Content-Type": "text/html",
            "Set-Cookie": "session=abc",
            "X-Anything": "1",
            "Strict-Transport-Security": "max-age=0",
            "Random": "drop",
            "Date": "also drop",
        }
    )
    assert filtered == {
        "Server": "nginx",
        "Content-Type": "text/html",
        "Set-Cookie": "session=abc",
        "X-Anything": "1",
        "Strict-Transport-Security": "max-age=0",
    }


def test_classify_connection_error_unknown_falls_back() -> None:
    """Unknown messages classify as ``other_error`` rather than raising."""
    outcome = _classify_connection_error(requests.exceptions.ConnectionError("mystery"))
    assert outcome == "other_error"
