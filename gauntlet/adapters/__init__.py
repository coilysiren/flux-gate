from __future__ import annotations

from typing import Protocol

from ..models import Action, HttpRequest, HttpResponse, Observation
from .http import HttpApi

__all__ = ["Adapter", "HttpApi"]


class Adapter(Protocol):
    """Executes an action against the system under test and returns an observation."""

    def send(self, user: str, request: HttpRequest) -> HttpResponse: ...

    def execute(self, user: str, action: Action) -> Observation: ...
