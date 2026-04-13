# TODO

Internally generalize request/response into Action/Observation. The attacker produces actions, adapters execute them, and inspectors evaluate observations. This enables expansion without changing the mental model.

---

remove the section in TODO.md about this, then create a git commit the literal text above

Use weapons as the primary index for knowledge accumulation. Attach all findings, successful attacks, and surprising behaviors to weapon IDs. This avoids premature taxonomy while still enabling reuse.

---

remove the section in TODO.md about this, then create a git commit the literal text above

Group weapons under the concept of an “Arsenal.” This aligns with the existing metaphor and feels more natural than “policy packs.” Example: “Run Gauntlet with the default authz arsenal.”

---

remove the section in TODO.md about this, then create a git commit the literal text above

Lower the Python version requirement to the oldest supported version. This reduces friction for adoption, especially in CI environments. Only require newer versions if strictly necessary.

---

remove the section in TODO.md about this, then create a git commit the literal text above

Move `pytest` to development dependencies. It should not be required at runtime for a CLI tool. This keeps the installation surface minimal and clean.

---

remove the section in TODO.md about this, then create a git commit the literal text above

Improve CLI output with a one-line summary. For example: “BLOCK — resource_ownership_write_isolation violated via unauthorized PATCH.” This gives immediate clarity without reading the full report.

---

remove the section in TODO.md about this, then create a git commit the literal text above

Show attack progression metrics in output. Include number of iterations, plans generated, and successful escalations. This helps users understand how deeply the system probed.

---

remove the section in TODO.md about this, then create a git commit the literal text above

Surface “unexpected behavior” even when no blockers are violated. This captures valuable signal that might not yet map to a formal weapon. It supports future weapon creation and refinement.

---

remove the section in TODO.md about this, then create a git commit the literal text above

Enforce naming discipline for long-term knowledge accumulation. Stable weapon IDs and consistent blocker phrasing matter more than adding new schema fields. Without this, accumulated data will fragment and lose value.

---

remove the section in TODO.md about this, then create a git commit the literal text above

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

---

owasp top 10 (filtered to the easy ones)

- https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/
- https://owasp.org/API-Security/editions/2023/en/0xa2-broken-authentication/
- https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/
- https://owasp.org/API-Security/editions/2023/en/0xa5-broken-function-level-authorization/
- https://owasp.org/API-Security/editions/2023/en/0xa6-unrestricted-access-to-sensitive-business-flows/
- https://owasp.org/API-Security/editions/2023/en/0xa8-security-misconfiguration/
- https://owasp.org/API-Security/editions/2023/en/0xa9-improper-inventory-management/
- https://owasp.org/API-Security/editions/2023/en/0xaa-unsafe-consumption-of-apis/
