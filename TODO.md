# TODO

*** I'm going to give you a wall of TODOs. Implement them all as sub-agents with as little shared context as technically possibe ***

Internally generalize request/response into Action/Observation. The attacker produces actions, adapters execute them, and inspectors evaluate observations. This enables expansion without changing the mental model.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Keep the 4-iteration ladder but explicitly define its intent. Label them as baseline, boundary, adversarial misuse, and targeted escalation. This improves interpretability without changing behavior.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Use weapons as the primary index for knowledge accumulation. Attach all findings, successful attacks, and surprising behaviors to weapon IDs. This avoids premature taxonomy while still enabling reuse.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Group weapons under the concept of an "Arsenal." This aligns with the existing metaphor and feels more natural than "policy packs." Example: "Run Gauntlet with the default authz arsenal."

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Lower the Python version requirement to the oldest supported version. This reduces friction for adoption, especially in CI environments. Only require newer versions if strictly necessary.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Move `pytest` to development dependencies. It should not be required at runtime for a CLI tool. This keeps the installation surface minimal and clean.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Improve CLI output with a one-line summary. For example: "BLOCK — resource_ownership_write_isolation violated via unauthorized PATCH." This gives immediate clarity without reading the full report.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Show attack progression metrics in output. Include number of iterations, plans generated, and successful escalations. This helps users understand how deeply the system probed.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Surface "unexpected behavior" even when no blockers are violated. This captures valuable signal that might not yet map to a formal weapon. It supports future weapon creation and refinement.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Enforce naming discipline for long-term knowledge accumulation. Stable weapon IDs and consistent blocker phrasing matter more than adding new schema fields. Without this, accumulated data will fragment and lose value.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Define core positioning: "Find real vulnerabilities in your API using adversarial AI". This should be the one-line hook that immediately communicates value to someone skimming GitHub or Hacker News. Avoid internal terminology like "attack plans" or "dark factory" in the primary pitch; those can live deeper in docs.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Define clear, reproducible failure cases in those targets (auth gaps, ownership leaks, validation issues). Each flaw should be deterministic and triggerable on demand. Avoid randomness here so demos and CI runs are consistent.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Implement OpenAPI ingestion and endpoint enumeration. Parse the spec into a structured internal model that can drive request generation. This becomes the backbone for all attack execution. Design and implement CLI entrypoint (e.g. `gauntlet run <openapi>`). The CLI should feel obvious and require zero configuration beyond pointing at a spec or URL. Treat this as the primary interface—everything else is secondary. Lock scope to REST APIs with OpenAPI input. Do not try to support arbitrary APIs or GraphQL in v0—this will dilute focus and slow execution. OpenAPI gives you a structured surface for deterministic exploration and is widely adopted.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Implement 5 core weapons: identity_swap, state_desync, temporal_replay, schema_mutation, semantic_conflict. These should cover ownership, state, timing, validation, and logic respectively. Resist adding more until these are solid and producing good output.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Define weapon interface (inputs, mutation strategy, expected signals). Each weapon should take a baseline request and produce mutated variants plus evaluation criteria. This abstraction is what enables future extensibility.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Map weapons to human-readable findings (clear, concise output). Translate low-level request/response differences into meaningful explanations. The output should read like a human describing a bug, not a diff dump.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Standardize output format (e.g. ❌ issue / ⚠️ warning with explanation). Consistency here improves readability and shareability. This format should be optimized for screenshots and copy-paste.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Generate realistic but deterministic findings for demo reliability. Even if internally heuristic-driven, the output should be stable across runs. Flaky demos will destroy trust quickly.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Create a clean README with: hook, demo, quickstart, example output. The README is your primary conversion surface, not your code. Optimize it for scanning and immediate understanding.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Expose attack primitives as pluggable "weapons". Design this early so contributors can extend the system without touching core logic. This is key to long-term growth.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Optimize for deterministic, repeatable demo runs. Every demo should produce the same results given the same input. This builds trust and avoids confusion.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Frame tool as "automated adversarial thinking" rather than security scanner. This broadens appeal beyond security engineers. It positions Gauntlet as a new category rather than a crowded one.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Allow configs entirely via yaml file

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

Add CONTRIBUTING.md with clear extension points for new weapons. Be explicit about how to add a weapon, what interface to implement, and how to test it. Lowering contribution friction is critical.

---

remove the section in TODO.md about this, then create a git commit the literal text above, be aware that I'm performing multiple agentic pull requests on this same codebase at the same time.

owasp top 10 (filtered to the easy ones)

- https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/
- https://owasp.org/API-Security/editions/2023/en/0xa2-broken-authentication/
- https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/
- https://owasp.org/API-Security/editions/2023/en/0xa5-broken-function-level-authorization/
- https://owasp.org/API-Security/editions/2023/en/0xa6-unrestricted-access-to-sensitive-business-flows/
- https://owasp.org/API-Security/editions/2023/en/0xa8-security-misconfiguration/
- https://owasp.org/API-Security/editions/2023/en/0xa9-improper-inventory-management/
- https://owasp.org/API-Security/editions/2023/en/0xaa-unsafe-consumption-of-apis/
