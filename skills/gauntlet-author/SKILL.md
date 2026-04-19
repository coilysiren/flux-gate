---
name: gauntlet-author
description: Author Gauntlet Weapon YAMLs from a product spec. Use this skill when the user wants to translate a verbose product specification, design doc, or natural-language description of an HTTP service into testable invariants packaged as Gauntlet Weapons. Triggers include "author weapons from this spec", "generate gauntlet weapons", "propose weapons for this API", "make weapons from this design doc", "what should we test about this service".
---

# Gauntlet Author

This skill takes a product spec — markdown, plain text, or a path to one — and produces a `.gauntlet/weapons/` directory of Weapon YAMLs ready to feed the Gauntlet hardening loop. The skill is the translation layer between "what the system is supposed to do" (a human-authored spec) and "what externally observable invariants we will check" (Gauntlet Weapons).

You are reasoning about *invariants*, not endpoints. The Weapon describes the property that must hold; the host's Attacker subagent will figure out how to exercise the API surface.

## Critical: Weapons own the train/test split

Each Weapon has two halves:

- `description` — the attack surface, given to the Attacker. Phrased as "the API enforces X." The Attacker uses this to compose probes.
- `blockers` — the falsifiable acceptance criteria, withheld from the Attacker and given only to the HoldoutEvaluator. Phrased as "A request that does X is rejected with status 403."

If both halves describe the same thing, the holdout adds no information beyond what the Attacker already has. **They must be different angles on the same invariant.** The description is a generality; each blocker is a concrete, observable check.

If you write a description that gives away the blockers, you have collapsed the train/test split before the run even starts. Re-write.

## What to do when invoked

### Step 1 — Get the spec

The host gives you one of:

- a file path (read it via `Read`),
- inline spec text in the dispatch prompt,
- a directory containing related docs (read each via `Read`).

If the spec is missing or empty, surface that and stop. Do not invent invariants from no source.

### Step 2 — Identify testable invariants

Read the spec end-to-end before writing anything. Look for properties that:

- can be falsified by an external HTTP observation (status code, response body shape, side-effect on subsequent reads),
- have a clear "should" or "must" in the spec, or are implicit in domain language ("only the owner can ...", "the cart must total ..."),
- generalize across endpoints (one invariant tested at many endpoints is more valuable than one endpoint tested for many invariants).

Common invariant categories — use this as a checklist, not a script:

| Category | Example invariants |
|---|---|
| Authorization | "Users cannot read or modify other users' resources." |
| Ownership | "A resource is mutable only by its creator." |
| Input validation | "Required fields are required. Type mismatches are rejected." |
| State transitions | "A completed order cannot be cancelled. A draft cannot be billed." |
| Read isolation | "Listing resources returns only those the caller can read." |
| Idempotency | "Re-submitting the same operation does not duplicate side effects." |
| Error boundaries | "Internal failures surface as 5xx with a stable error shape, not 200." |

If the spec mentions a specific failure mode ("must reject malformed JSON with 400"), that is a blocker, not an invariant on its own — fold it into the relevant category.

If you find five candidate invariants, prefer five over fifty. Each weapon adds 4+ iteration cycles of cost. A small, sharp arsenal that surfaces the first wave of regressions is more valuable than a sprawling one that exhausts the budget on shallow checks.

### Step 3 — Write each Weapon as YAML

Authoring location: `.gauntlet/weapons/<slug>.yaml`. One file per weapon. Slug is snake_case derived from the invariant.

Required fields, per the `Weapon` Pydantic model in [`gauntlet/models.py`](../../gauntlet/models.py):

```yaml
id: resource_ownership_write_isolation     # snake_case, stable across runs
title: Users cannot modify each other's resources
description: >
  The API must enforce resource ownership across every write endpoint
  (PATCH, PUT, DELETE). A user who did not create a resource must not be
  able to mutate it through any documented route, including bulk operations.
blockers:
  - A PATCH request by a non-owner is rejected with 403 or 404
  - A DELETE request by a non-owner is rejected with 403 or 404
  - The resource body is unchanged after an unauthorized write attempt
  - A GET by the owner after a failed unauthorized write returns the original data
inspired_by: spec.md#authorization
```

Authoring rules:

- `id` is snake_case and stable. The risk-report assembler keys findings by id; if you rename the id between runs, history breaks.
- `title` is human-readable, sentence-case, no period at the end.
- `description` is what the Attacker sees. It describes the **surface** and the **invariant** — not the acceptance criteria.
- Each `blocker` is a single falsifiable statement with an expected status code where applicable. "Returns 403" is testable; "is secure" is not.
- `inspired_by` is optional; use it to cite the spec section that drove this weapon. Helpful for review.
- Do **not** invent endpoint paths. The Weapon is surface-agnostic; endpoints come from the host's `Target` (which it knows better than you do).
- Do **not** include sample requests or expected responses in the YAML. That is the Attacker's job to compose at run time.

### Step 4 — Preflight each Weapon

For each weapon you wrote, call `mcp__gauntlet__assess_weapon(weapon_id, target=None)`. The assessor is heuristic and lenient — `proceed=False` is a real signal something is off (vague blockers, missing status codes). If it fails, **do not silently fix** — surface the issues to the user with the specific weapon id. Common fixes:

- A blocker shorter than 20 chars is too vague; add the expected behaviour.
- No HTTP status codes anywhere in blockers; usually means you're describing intent ("auth check happens") instead of behaviour ("returns 403").

You can safely re-run preflight after edits — it has no side effects.

### Step 5 — Stop before authoring Targets

Targets are the API-surface specifics (endpoint paths, methods). The host knows its API better than you do. If the spec mentions an OpenAPI file, point the host at it (`list_targets(openapi_path=...)`); otherwise tell the user that targets need to be authored by hand or generated from an OpenAPI spec.

Do not generate targets even if the spec lists endpoints. The host will compose them when it runs the loop.

### Step 6 — Summarize

Return to the user (or orchestrator):

- Number of weapons authored.
- One-line summary per weapon (id + title).
- Any preflight failures and why.
- Suggested next steps: provide a target / OpenAPI path, then run the gauntlet skill.

## What this skill does not do

- It does not run the adversarial loop. Authoring and running are separate concerns; the host composes them.
- It does not score the authored weapons for coverage. (That's a future feature; for now, human review.)
- It does not write Targets, Arsenals, or users.yaml.
- It does not invoke any LLM other than itself — no API calls, no separate Anthropic/OpenAI clients.
- It does not modify weapons that already exist in `.gauntlet/weapons/`. If a weapon with the same id is already there, surface the conflict and let the user decide; don't overwrite.

## Common mistakes to avoid

- **Description leaks blockers.** "The API rejects non-owner PATCH with 403" in the description means the Attacker reads the blocker for free. Re-write the description to "The API enforces resource ownership on writes."
- **Blocker is implementation, not behaviour.** "Goes through the auth middleware" is not externally observable; "returns 403" is.
- **One weapon per endpoint instead of per invariant.** If "POST /tasks" and "POST /projects" both need the same auth check, that's *one* weapon describing the invariant ("Writes require authentication"), not two.
- **Authoring fifty weapons from a five-page spec.** Start with the five sharpest invariants. The user can ask for more if those land.
- **Editing existing weapons mid-run.** Each weapon's blockers anchor the holdout. If they churn, the holdout is no longer a holdout.
