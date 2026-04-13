from __future__ import annotations

from ..models import Action, HttpRequest, HttpResponse, Observation


class CliAdapter:
    """Execution adapter for CLI surfaces. Not yet implemented."""

    def send(self, user: str, request: HttpRequest) -> HttpResponse:
        raise NotImplementedError

    def execute(self, user: str, action: Action) -> Observation:
        raise NotImplementedError
