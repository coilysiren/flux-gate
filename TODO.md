# TODO

Avoid expanding the weapon schema, but treat weapons as anchors for accumulated knowledge. Store past successful and failed attack patterns keyed by weapon ID. This allows learning over time without introducing heavy metadata structures.

---

then create a git commit with that literal text

Make attack plans first-class artifacts that persist beyond a single run. Deduplicate and reuse them across executions, and track lineage between plans. This turns the system from ephemeral exploration into a compounding knowledge engine.

---

then create a git commit with that literal text

Add lightweight plan lineage tracking. Each plan should reference the iteration and parent plan it evolved from. This helps explain escalation behavior and improves debuggability.

---

then create a git commit with that literal text

Upgrade findings to include evidence-grade outputs. Each finding should include reproduction steps, request/response traces, actor context, and the violated blocker. This makes results actionable instead of just descriptive.

---

then create a git commit with that literal text

Replace freeform evidence strings with structured evidence items. Introduce a minimal schema (e.g. request, response, assertion, note) without overcomplicating it. This enables replay and better downstream tooling.

---

then create a git commit with that literal text

Add a replay bundle concept for findings. Store a minimal sequence of actions and inputs that can reproduce the issue. Full determinism is not required yet, but reproducibility should be possible.

---

then create a git commit with that literal text

Introduce an Adapter abstraction for execution surfaces. HTTP becomes one adapter, with CLI and WebDriver as next candidates. This decouples the adversarial loop from any single interface.

---

then create a git commit with that literal text

Internally generalize request/response into Action/Observation. The attacker produces actions, adapters execute them, and inspectors evaluate observations. This enables expansion without changing the mental model.

---

then create a git commit with that literal text

Keep the 4-iteration ladder but explicitly define its intent. Label them as baseline, boundary, adversarial misuse, and targeted escalation. This improves interpretability without changing behavior.

---

then create a git commit with that literal text

Use weapons as the primary index for knowledge accumulation. Attach all findings, successful attacks, and surprising behaviors to weapon IDs. This avoids premature taxonomy while still enabling reuse.

---

then create a git commit with that literal text

Group weapons under the concept of an “Arsenal.” This aligns with the existing metaphor and feels more natural than “policy packs.” Example: “Run Gauntlet with the default authz arsenal.”

---

then create a git commit with that literal text

Lower the Python version requirement to the oldest supported version. This reduces friction for adoption, especially in CI environments. Only require newer versions if strictly necessary.

---

then create a git commit with that literal text

Move `pytest` to development dependencies. It should not be required at runtime for a CLI tool. This keeps the installation surface minimal and clean.

---

then create a git commit with that literal text

Improve CLI output with a one-line summary. For example: “BLOCK — resource_ownership_write_isolation violated via unauthorized PATCH.” This gives immediate clarity without reading the full report.

---

then create a git commit with that literal text

Show attack progression metrics in output. Include number of iterations, plans generated, and successful escalations. This helps users understand how deeply the system probed.

---

then create a git commit with that literal text

Surface “unexpected behavior” even when no blockers are violated. This captures valuable signal that might not yet map to a formal weapon. It supports future weapon creation and refinement.

---

then create a git commit with that literal text

Enforce naming discipline for long-term knowledge accumulation. Stable weapon IDs and consistent blocker phrasing matter more than adding new schema fields. Without this, accumulated data will fragment and lose value.

---

then create a git commit with that literal text

---

- installation should assume we aren't testing against a python project
- end to end pipeline examples, eg. full dark factory workflow, should include `AGENTS.md` guidance on how to use it
- readme out of sync with github description
- shorter cuter descriptions can be written now that we have the inspector weapon target wording
- litellm
- docker hub
- homebrew
- pypi

actually use it !!!
