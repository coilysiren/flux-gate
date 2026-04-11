from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from anthropic import Anthropic
from anthropic.types import TextBlock
from openai import OpenAI

from .models import (
    Assertion,
    EvidenceItem,
    ExecutionResult,
    Finding,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Plan,
    PlanStep,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_ATTACKER_SYSTEM = """\
You are a software testing Attacker in an adversarial quality-control loop.
Your job is to generate realistic HTTP API test plans that probe for weaknesses.

IMPORTANT — train/test separation:
You receive only a weapon description (what surface to attack). The acceptance
criteria that determine the final pass/block decision are intentionally withheld
from you. Do not attempt to infer or reconstruct them. Your value comes from
exploring the attack surface freely, without knowing what the evaluator will
check. Plans that aim to "satisfy the hidden criteria" rather than genuinely
probe the system undermine the separation and will be discarded.

Respond with ONLY a valid JSON object matching this schema exactly:
{
  "plans": [
    {
      "name": "snake_case_identifier",
      "category": "authz|crud|boundary|lifecycle",
      "goal": "one-sentence description of what this plan tests",
      "steps": [
        {
          "user": "userA",
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
- Users are "userA" and "userB"
- Use {task_id} as a path template variable — it resolves from the "id" field in the first
  POST /tasks response body
- Assertion kind "status_code" requires an integer "expected" field and null "rule"
- Assertion kind "rule" requires rule "task_not_modified_by_other_user" and null "expected"
  (checks that last_modified_by == owner on a GET /tasks/{task_id} response)
- step_index is 1-based
- Generate 2–4 plans per call; prefer variety over repetition
"""

_INSPECTOR_SYSTEM = """\
You are a security Inspector in an adversarial quality-control loop.
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
      "evidence": [{"kind": "request|response|assertion|note", "content": "specific observation"}],
      "reproduction_steps": [
        "Step 1: POST /tasks as userA to create a task",
        "Step 2: PATCH /tasks/{id} as userB — expect 403, got 200"
      ],
      "violated_blocker": "exact blocker text that was violated, or null if unknown",
      "plan_name": "name of the plan whose steps demonstrate this finding"
    }
  ]
}

Severity guide:
- critical: auth bypass, cross-user data mutation, data corruption
- high: privilege escalation, sensitive data exposure
- medium: information leak, unexpected state change
- low: minor policy violation, cosmetic issue

Guidelines for evidence-grade findings:
- reproduction_steps must be ordered, concrete steps an engineer can follow to reproduce the issue
- violated_blocker must quote the exact expected-behavior statement that was violated, or null
- plan_name must match one of the plan names in the execution results so traces can be attached

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
# Public attacker and inspector
# ---------------------------------------------------------------------------


class LLMAttacker:
    """LLM-backed Attacker that generates test plans via an LLM API.

    Configure via env vars and instantiate with ``create_attacker()``.
    """

    def __init__(self, backend: _LLMBackend) -> None:
        self._backend = backend

    def generate_plans(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Plan]:
        user = _attacker_user_prompt(spec, previous_iterations)
        raw = self._backend.complete(_ATTACKER_SYSTEM, user)
        return _parse_plans(raw)


class LLMInspector:
    """LLM-backed Inspector that analyzes execution results via an LLM API.

    Configure via env vars and instantiate with ``create_inspector()``.
    """

    def __init__(self, backend: _LLMBackend) -> None:
        self._backend = backend

    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]:
        user = _inspector_user_prompt(spec, execution_results)
        raw = self._backend.complete(_INSPECTOR_SYSTEM, user)
        return _parse_findings(raw, execution_results)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-opus-4-5",
}


def create_attacker(provider: str, api_key: str) -> LLMAttacker:
    """Instantiate an ``LLMAttacker`` for the given provider.

    Args:
        provider: ``"openai"`` or ``"anthropic"``
        api_key:  API key for the provider.
    """
    return LLMAttacker(_make_backend(provider, api_key))


def create_inspector(provider: str, api_key: str) -> LLMInspector:
    """Instantiate an ``LLMInspector`` for the given provider.

    Args:
        provider: ``"openai"`` or ``"anthropic"``
        api_key:  API key for the provider.
    """
    return LLMInspector(_make_backend(provider, api_key))


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


def _attacker_user_prompt(spec: IterationSpec, previous_iterations: list[IterationRecord]) -> str:
    parts = [
        f"## Iteration {spec.index}: {spec.name}",
        f"Goal: {spec.goal}",
        f"Instruction: {spec.attacker_prompt}",
    ]
    if spec.weapon:
        parts.append(f"Weapon: {spec.weapon.description.strip()}")
    if spec.target and spec.target.endpoints:
        parts.append(f"Target endpoints: {', '.join(spec.target.endpoints)}")

    if previous_iterations:
        parts.append("\n## Previous Findings")
        for record in previous_iterations:
            for finding in record.findings:
                parts.append(f"- [{finding.severity}] {finding.issue}: {finding.rationale}")
            if not record.findings:
                parts.append(f"- Iteration {record.spec.index}: no findings")

    parts.append("\nGenerate test plans.")
    return "\n".join(parts)


def _inspector_user_prompt(spec: IterationSpec, execution_results: list[ExecutionResult]) -> str:
    parts = [
        f"## Iteration {spec.index}: {spec.name}",
        f"Instruction: {spec.inspector_prompt}",
        "\n## Execution Results",
    ]
    for result in execution_results:
        parts.append(f"\n### Plan: {result.plan_name}")
        for step in result.steps:
            parts.append(
                f"  Step {step.step_index} ({step.user}): "
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


def _parse_plans(raw: str) -> list[Plan]:
    try:
        data: dict[str, Any] = json.loads(raw)
        plans: list[Plan] = []
        for s in data.get("plans", []):
            steps = [
                PlanStep(
                    user=step["user"],
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
            plans.append(
                Plan(
                    name=s["name"],
                    category=s["category"],
                    goal=s["goal"],
                    steps=steps,
                    assertions=assertions,
                )
            )
        return plans
    except Exception:  # noqa: BLE001
        logger.warning("Failed to parse LLM attacker response", exc_info=True)
        return []


def _parse_findings(
    raw: str, execution_results: list[ExecutionResult] | None = None
) -> list[Finding]:
    try:
        data: dict[str, Any] = json.loads(raw)
        results_by_name = {r.plan_name: r for r in (execution_results or [])}
        findings: list[Finding] = []
        for f in data.get("findings", []):
            plan_name = f.get("plan_name", "")
            matched = results_by_name.get(plan_name)
            traces = matched.steps if matched else []
            findings.append(
                Finding(
                    issue=f["issue"],
                    severity=f["severity"],
                    confidence=float(f.get("confidence", 0.5)),
                    rationale=f["rationale"],
                    next_targets=f.get("next_targets", []),
                    evidence=[
                        EvidenceItem(kind=e["kind"], content=e["content"])
                        for e in f.get("evidence", [])
                        if isinstance(e, dict)
                    ],
                    reproduction_steps=f.get("reproduction_steps", []),
                    traces=traces,
                    violated_blocker=f.get("violated_blocker"),
                )
            )
        return findings
    except Exception:  # noqa: BLE001
        logger.warning("Failed to parse LLM inspector response", exc_info=True)
        return []
