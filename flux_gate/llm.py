from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from anthropic import Anthropic
from anthropic.types import TextBlock
from openai import OpenAI

from .models import (
    Assertion,
    ExecutionResult,
    Finding,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Scenario,
    ScenarioStep,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_OPERATOR_SYSTEM = """\
You are a software testing Operator in an adversarial quality-control loop.
Your job is to generate realistic HTTP API test scenarios that probe for weaknesses.

Respond with ONLY a valid JSON object matching this schema exactly:
{
  "scenarios": [
    {
      "name": "snake_case_identifier",
      "category": "authz|crud|boundary|lifecycle",
      "goal": "one-sentence description of what this scenario tests",
      "steps": [
        {
          "actor": "userA",
          "request": {
            "method": "GET|POST|PATCH",
            "path": "/tasks/{task_id}",
            "body": {}
          }
        }
      ],
      "assertions": [
        {
          "name": "assertion_identifier",
          "kind": "status_code",
          "expected": 403,
          "rule": null,
          "step_index": 2
        }
      ]
    }
  ]
}

Rules:
- Actors are "userA" and "userB"
- Use {task_id} as a path template variable — it resolves from the "id" field in the first
  POST /tasks response body
- Assertion kind "status_code" requires an integer "expected" field and null "rule"
- Assertion kind "guard" requires rule "task_not_modified_by_other_user" and null "expected"
  (checks that last_modified_by == owner on a GET /tasks/{task_id} response)
- step_index is 1-based
- Generate 2–4 scenarios per call; prefer variety over repetition
"""

_ADVERSARY_SYSTEM = """\
You are a security Adversary in an adversarial quality-control loop.
You analyze HTTP API test results and surface security weaknesses, logic failures,
and guard violations.

Respond with ONLY a valid JSON object matching this schema exactly:
{
  "findings": [
    {
      "issue": "snake_case_identifier",
      "severity": "low|medium|high|critical",
      "confidence": 0.85,
      "rationale": "explanation of why this is a problem",
      "next_targets": ["area to probe next"],
      "evidence": ["specific observation from the execution results"]
    }
  ]
}

Severity guide:
- critical: auth bypass, cross-user data mutation, data corruption
- high: privilege escalation, sensitive data exposure
- medium: information leak, unexpected state change
- low: minor policy violation, cosmetic issue

Return an empty findings list if nothing suspicious was observed.
"""

# ---------------------------------------------------------------------------
# LLM backend abstraction
# ---------------------------------------------------------------------------


class _LLMBackend(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class _OpenAIBackend:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""


class _AnthropicBackend:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        block = message.content[0]
        if isinstance(block, TextBlock):
            return block.text
        return ""


# ---------------------------------------------------------------------------
# Public operator and adversary
# ---------------------------------------------------------------------------


class LLMOperator:
    """LLM-backed Operator that generates test scenarios via an LLM API.

    Configure via env vars and instantiate with ``create_operator()``.
    """

    def __init__(self, backend: _LLMBackend) -> None:
        self._backend = backend

    def generate_scenarios(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Scenario]:
        user = _operator_user_prompt(spec, previous_iterations)
        raw = self._backend.complete(_OPERATOR_SYSTEM, user)
        return _parse_scenarios(raw)


class LLMAdversary:
    """LLM-backed Adversary that analyzes execution results via an LLM API.

    Configure via env vars and instantiate with ``create_adversary()``.
    """

    def __init__(self, backend: _LLMBackend) -> None:
        self._backend = backend

    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]:
        user = _adversary_user_prompt(spec, execution_results)
        raw = self._backend.complete(_ADVERSARY_SYSTEM, user)
        return _parse_findings(raw)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-opus-4-5",
}


def create_operator(provider: str, api_key: str) -> LLMOperator:
    """Instantiate an ``LLMOperator`` for the given provider.

    Args:
        provider: ``"openai"`` or ``"anthropic"``
        api_key:  API key for the provider.
    """
    return LLMOperator(_make_backend(provider, api_key))


def create_adversary(provider: str, api_key: str) -> LLMAdversary:
    """Instantiate an ``LLMAdversary`` for the given provider.

    Args:
        provider: ``"openai"`` or ``"anthropic"``
        api_key:  API key for the provider.
    """
    return LLMAdversary(_make_backend(provider, api_key))


def _make_backend(provider: str, api_key: str) -> _LLMBackend:
    model = _DEFAULT_MODELS.get(provider, "")
    if provider == "openai":
        return _OpenAIBackend(api_key=api_key, model=model)
    if provider == "anthropic":
        return _AnthropicBackend(api_key=api_key, model=model)
    raise ValueError(f"Unknown LLM provider {provider!r}. Expected 'openai' or 'anthropic'.")


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _operator_user_prompt(spec: IterationSpec, previous_iterations: list[IterationRecord]) -> str:
    parts = [
        f"## Iteration {spec.index}: {spec.name}",
        f"Goal: {spec.goal}",
        f"Instruction: {spec.operator_prompt}",
    ]
    if spec.guard:
        parts.append(f"Guard: {spec.guard.description.strip()}")
        if spec.guard.target_endpoints:
            parts.append(f"Target endpoints: {', '.join(spec.guard.target_endpoints)}")

    if previous_iterations:
        parts.append("\n## Previous Findings")
        for record in previous_iterations:
            for finding in record.findings:
                parts.append(f"- [{finding.severity}] {finding.issue}: {finding.rationale}")
            if not record.findings:
                parts.append(f"- Iteration {record.spec.index}: no findings")

    parts.append("\nGenerate test scenarios.")
    return "\n".join(parts)


def _adversary_user_prompt(spec: IterationSpec, execution_results: list[ExecutionResult]) -> str:
    parts = [
        f"## Iteration {spec.index}: {spec.name}",
        f"Instruction: {spec.adversary_prompt}",
        "\n## Execution Results",
    ]
    for result in execution_results:
        parts.append(f"\n### Scenario: {result.scenario_name}")
        for step in result.steps:
            parts.append(
                f"  Step {step.step_index} ({step.actor}): "
                f"{step.request.method} {step.request.path} → {step.response.status_code}"
            )
            if step.response.body:
                parts.append(f"    Response: {json.dumps(step.response.body)}")
        for assertion in result.assertions:
            status = "PASS" if assertion.passed else "FAIL"
            parts.append(f"  [{status}] {assertion.name}: {assertion.detail}")
    parts.append("\nAnalyze the results and return your findings.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


def _parse_scenarios(raw: str) -> list[Scenario]:
    try:
        data: dict[str, Any] = json.loads(raw)
        scenarios: list[Scenario] = []
        for s in data.get("scenarios", []):
            steps = [
                ScenarioStep(
                    actor=step["actor"],
                    request=HttpRequest(
                        method=step["request"]["method"],
                        path=step["request"]["path"],
                        body=step["request"].get("body") or {},
                    ),
                )
                for step in s.get("steps", [])
            ]
            assertions = [
                Assertion(
                    name=a["name"],
                    kind=a["kind"],
                    expected=a.get("expected"),
                    rule=a.get("rule"),
                    step_index=a["step_index"],
                )
                for a in s.get("assertions", [])
            ]
            scenarios.append(
                Scenario(
                    name=s["name"],
                    category=s["category"],
                    goal=s["goal"],
                    steps=steps,
                    assertions=assertions,
                )
            )
        return scenarios
    except Exception:  # noqa: BLE001
        logger.warning("Failed to parse LLM operator response", exc_info=True)
        return []


def _parse_findings(raw: str) -> list[Finding]:
    try:
        data: dict[str, Any] = json.loads(raw)
        findings: list[Finding] = []
        for f in data.get("findings", []):
            findings.append(
                Finding(
                    issue=f["issue"],
                    severity=f["severity"],
                    confidence=float(f.get("confidence", 0.5)),
                    rationale=f["rationale"],
                    next_targets=f.get("next_targets", []),
                    evidence=f.get("evidence", []),
                )
            )
        return findings
    except Exception:  # noqa: BLE001
        logger.warning("Failed to parse LLM adversary response", exc_info=True)
        return []
