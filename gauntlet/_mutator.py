"""Deterministic across-iteration plan mutator.

The Attacker subagent composes plans in-prompt from the iteration buffer.
Across iterations, re-derivation-from-scratch can under-explore: the same
baseline plan keeps showing up and edge cases never surface. This module
takes plans that have already landed and produces variants for the next
iteration without spending LLM tokens on the mutation step.

Strategies (applied independently, up to ``max_variants`` variants per call):

1. ``drop_field`` — pick one step whose request body is non-empty, remove
   one key from the body.
2. ``swap_users`` — rotate ``user`` values across steps, if the plan uses
   at least two distinct users.
3. ``toggle_expected`` — flip every assertion's ``expected`` to
   ``{"not": original}``, using the new matcher shape.
4. ``reverse_order`` — reverse the step list. If reverse produces the same
   ordering (e.g. a one-step plan), skip.

Determinism: given the same input plans and the same ``max_variants``, the
output is byte-for-byte identical. Randomness is seeded per-plan from a
hash of the plan name, not from any global RNG.

No train/test risk: the mutator reads only plans the Attacker has already
seen (via ``read_iteration_records``). It does not see blocker text, holdout
results, or any Inspector output it isn't allowed to read.
"""

from __future__ import annotations

import copy
import hashlib
import random

from .models import Assertion, Plan

_STRATEGIES = (
    "drop_field",
    "swap_users",
    "toggle_expected",
    "reverse_order",
)


def mutate_plans(seed_plans: list[Plan], *, max_variants: int = 4) -> list[Plan]:
    """Return up to ``max_variants`` deterministic mutants of ``seed_plans``.

    Variants are produced by applying each strategy in ``_STRATEGIES`` order
    to every seed plan. A variant is dropped if it would be identical to its
    seed (i.e. the mutation was a no-op — e.g. reversing a one-step plan).
    The returned list is bounded by ``max_variants`` and preserves the
    strategy order: every strategy gets a chance at one plan before the
    next strategy runs.

    The ``name`` of every returned plan is ``<seed_name>:mut-<strategy>`` so
    variants remain distinguishable in the iteration buffer.
    """
    if not seed_plans or max_variants <= 0:
        return []

    variants: list[Plan] = []
    # Iterate strategy-major so a limited max_variants still covers a mix
    # of strategies rather than exhausting one strategy on every seed.
    for strategy in _STRATEGIES:
        for seed in seed_plans:
            if len(variants) >= max_variants:
                return variants
            mutant = _apply(strategy, seed)
            if mutant is None:
                continue
            variants.append(mutant)
    return variants


def _apply(strategy: str, seed: Plan) -> Plan | None:
    rng = _seeded_rng(seed.name, strategy)
    if strategy == "drop_field":
        return _mutate_drop_field(seed, rng)
    if strategy == "swap_users":
        return _mutate_swap_users(seed)
    if strategy == "toggle_expected":
        return _mutate_toggle_expected(seed)
    if strategy == "reverse_order":
        return _mutate_reverse_order(seed)
    raise AssertionError(f"unknown mutation strategy {strategy!r}")


def _seeded_rng(plan_name: str, strategy: str) -> random.Random:
    digest = hashlib.sha256(f"{plan_name}|{strategy}".encode()).digest()
    # int.from_bytes is deterministic; 64 bits of entropy is plenty for the
    # one-or-two random choices each strategy makes.
    seed_int = int.from_bytes(digest[:8], "big")
    return random.Random(seed_int)


def _clone(seed: Plan, *, suffix: str) -> Plan:
    # deepcopy so each variant owns its own nested models (body dicts,
    # assertion objects) rather than aliasing the seed.
    mutant = copy.deepcopy(seed)
    return mutant.model_copy(update={"name": f"{seed.name}:mut-{suffix}"})


def _mutate_drop_field(seed: Plan, rng: random.Random) -> Plan | None:
    candidate_indices = [i for i, step in enumerate(seed.steps) if step.request.body]
    if not candidate_indices:
        return None
    mutant = _clone(seed, suffix="drop_field")
    target = candidate_indices[rng.randrange(len(candidate_indices))]
    body = dict(mutant.steps[target].request.body)
    if not body:
        return None
    # Sort keys so the per-plan RNG picks deterministically regardless of
    # dict insertion order.
    key = sorted(body.keys())[rng.randrange(len(body))]
    body.pop(key)
    mutant.steps[target].request.body = body
    if mutant.steps == seed.steps:
        return None
    return mutant


def _mutate_swap_users(seed: Plan) -> Plan | None:
    users = [step.user for step in seed.steps]
    if len(set(users)) < 2:
        return None
    mutant = _clone(seed, suffix="swap_users")
    # Rotate by one position; guaranteed to be a distinct permutation when
    # there are ≥ 2 distinct users.
    rotated = users[1:] + users[:1]
    if rotated == users:
        return None
    for step, new_user in zip(mutant.steps, rotated, strict=True):
        step.user = new_user
    return mutant


def _mutate_toggle_expected(seed: Plan) -> Plan | None:
    if not seed.assertions:
        return None
    mutant = _clone(seed, suffix="toggle_expected")
    new_assertions: list[Assertion] = []
    any_change = False
    for assertion in mutant.assertions:
        original = assertion.expected
        # Avoid double-negating an already-negated matcher so the mutator is
        # an involution-safe transformation.
        if isinstance(original, dict) and set(original.keys()) == {"not"}:
            new_assertions.append(assertion)
            continue
        negated: object = {"not": original}
        new_assertions.append(assertion.model_copy(update={"expected": negated}))
        any_change = True
    if not any_change:
        return None
    return mutant.model_copy(update={"assertions": new_assertions})


def _mutate_reverse_order(seed: Plan) -> Plan | None:
    if len(seed.steps) < 2:
        return None
    reversed_steps = list(reversed(seed.steps))
    if reversed_steps == seed.steps:
        return None
    mutant = _clone(seed, suffix="reverse_order")
    return mutant.model_copy(update={"steps": reversed_steps})


__all__ = ["mutate_plans"]
