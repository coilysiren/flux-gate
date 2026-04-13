from __future__ import annotations

from typing import Protocol

from ..models import Action, HttpRequest, HttpResponse, Observation
from .cli import CliAdapter
from .http import HttpApi, InMemoryHttpApi
from .webdriver import WebDriverAdapter

__all__ = [
    "Adapter",
    "CliAdapter",
    "HttpApi",
    "InMemoryHttpApi",
    "WebDriverAdapter",
]


class Adapter(Protocol):
    """Executes an action against the system under test and returns an observation.

    Adapters bridge the generalized Action/Observation layer to a concrete
    execution surface (HTTP, CLI, WebDriver, etc.).  The ``send`` method is
    the HTTP-specific shorthand; ``execute`` is the surface-agnostic entry
    point that the Drone prefers.
    """

    def send(self, user: str, request: HttpRequest) -> HttpResponse: ...

    def execute(self, user: str, action: Action) -> Observation: ...
