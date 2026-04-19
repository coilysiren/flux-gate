# TODO-v4: dark-factory alignment

Items surfaced by a read of the sibling project's architecture and investigation docs (codename internal; positioned as an agentic loop / "dark factory" consumer of Gauntlet). Each item here is too large, too contract-bearing, or too cross-cutting to one-shot in a single session. Batched into themes.

Companion context: the consumer's architecture-design doc frames Gauntlet as its hardening loop's quality gate, invoked as a CLI subprocess post-deploy. The integration points are the `.gauntlet/` directory, the CLI, and the YAML risk report. Everything below is in service of making those surfaces load-bearing for an orchestrator, not just a human on a laptop.

## 1. Subprocess contract hardening

Gauntlet is already usable from an orchestrator via the CLI, but the contract isn't yet specified crisply enough that an external agent can target it blindly.

- [ ] **Versioned risk-report schema.** Add `risk_report.schema_version: 1` (or equivalent) to the YAML output. Document semver-style compatibility rules. Orchestrators pin against the version; we can evolve the shape without breaking them.
- [ ] **`--json` output flag.** Orchestrators generally prefer JSON for programmatic consumption. YAML stays the default for humans; `--format json` (or `--json`) emits the same risk-report structure as JSON.
- [ ] **Stable, documented exit-code taxonomy.** Today: non-zero means "failure or critical finding." An orchestrator wants to distinguish "Gauntlet itself errored" (config missing, LLM unreachable) from "Gauntlet ran and found critical failures" from "ran clean, promote." Define: `0` = clearance, `1` = blocked by findings, `2` = runtime error, `3` = config/usage error. Document in usage.md.
- [ ] **Machine-readable artifact directory contract.** PlanStore and FindingsStore already write to disk; add a `--artifact-dir` flag so an orchestrator can pick the path and find everything at known locations. Document the layout in architecture.md.
- [ ] **Streaming progress output.** Long runs stall the orchestrator UI. Add an opt-in streaming mode (`--stream ndjson`) that emits one JSON event per iteration / finding / plan so a consumer can render live progress. Current all-at-end YAML stays the default.
- [ ] **Idempotency / resumability.** If a run crashes mid-iteration, can the orchestrator resume? Today: no. Consider a `--resume <run-id>` that reads PlanStore / FindingsStore state and picks up. Git-as-checkpoint is the consumer's idiom; our artifact store is the analog inside Gauntlet.

## 2. Single-provider as a first-class config

Right-now edit: softened the provider-diversity language in README, usage, architecture. Bigger work:

- [ ] **Explicit config surface.** Add `provider_diversity: optional|preferred|required` (or similar) to `.gauntlet/config.yaml`. Single-provider users set `optional`; CI users set `preferred` or `required`. A mismatch emits a warning (or error, for `required`). Makes the posture auditable rather than implicit.
- [ ] **LiteLLM integration** (already on TODO-v1/v2). Unblocks two things at once: one credential model for single-provider consumers, and a broader model matrix (e.g., Opus 4.7 for both roles inside a Claude Max subscription). Current `openai` / `anthropic` split is inherited from the initial prototype.
- [ ] **Inherit auth from parent Claude Code context.** If Gauntlet is invoked from inside a Claude Code session (plugin, subprocess, whatever), it should be able to resolve Anthropic credentials from the parent's auth context without `GAUNTLET_*_KEY` env vars. Needs thought — Claude Code's credential discovery isn't a stable public surface yet.
- [ ] **Blind-spot diagnostics for single-provider runs.** When both roles run on the same provider, track and report any correlated failure patterns (e.g., same refusal, same hallucination, identical plan). Gives single-provider consumers a signal about when to escalate to cross-provider.

## 3. Non-HTTP adapters

The consumer's v0 constrains to HTTP platforms (Slack-likes, TODO lists). But the Divergence in their architecture §6 explicitly flags Gauntlet's HTTP-only maturity as a constraint. If we want to enable platform types beyond HTTP REST, the stubs need to become real.

- [ ] **CliAdapter (real).** For CLI tools, Discord bots, anything that speaks stdin/stdout or argv. Attacker emits an action; CliAdapter runs the binary in a subprocess; Observation wraps exit code + stdout + stderr.
- [ ] **WebDriverAdapter (real).** For browser-driven apps. Playwright or Selenium under the hood. Attacker emits a plan like "click #submit, fill #email with X"; WebDriverAdapter executes.
- [ ] **Adapter-selection logic in the CLI / config.** Currently the HTTP adapter is implicit. Once two more exist, explicit selection: `--adapter http|cli|webdriver`, or auto-detect from the target spec.
- [ ] **Action/Observation payload stability across adapters.** Our `models.py` already defines Action/Observation as surface-agnostic wrappers. Make sure the non-HTTP adapters actually use those wrappers faithfully, not ad-hoc shapes.

## 4. Weapon authoring ergonomics (for programmatic Planners)

The consumer's Planner role derives Weapons from a product spec. Today our Weapon schema is human-authored YAML; making it machine-authorable cleanly is a win.

- [ ] **Publish a JSON Schema for Weapon YAML.** Ship `gauntlet/schemas/weapon.schema.json` so Planners can validate their output before running Gauntlet. Same for Target, Arsenal, users config.
- [ ] **`gauntlet validate` subcommand.** Dry-run: read the `.gauntlet/` directory, report schema violations, duplicate IDs, orphan targets. Orchestrators use this as a preflight gate.
- [ ] **Weapon-from-spec scaffolding helper.** An optional `gauntlet scaffold-weapon --description "users cannot modify each other's tasks"` that emits a Weapon YAML with blockers inferred from the description. The Planner still owns the authoritative version; this is a starting point, not a replacement.
- [ ] **Inline assertions inside Weapons for the train/test split.** Already in place via `blockers`; document explicitly that the Planner is the only authoring role and the Worker must not read the weapon files. (Usage.md now covers the workflow; a stronger statement in the Weapon schema itself — a comment or policy doc — would reinforce it.)

## 5. Integration-test the consumer contract

The consumer reads our risk report and routes findings back to its Planner. We should have an integration test that mimics this loop end-to-end.

- [ ] **Mock orchestrator harness.** `tests/integration/test_dark_factory_loop.py` (or renamed for stealth) that: (a) spawns the demo API, (b) runs Gauntlet, (c) parses the risk report, (d) verifies the parsed shape matches the documented contract, (e) simulates a "route failures back to Planner" step that asserts no blocker text leaks into the routed payload.
- [ ] **Contract drift CI.** A test that asserts the risk-report schema version matches the published version and that removing a field triggers a failure. Prevents silent breakage for consumers.
- [ ] **Demo end-to-end recording.** Once the mock harness is solid, record an asciinema / gif of the full loop for the docs site. Proves the contract works.

## 6. Claude Code plugin ergonomics

The consumer ships as a Claude Code plugin. Gauntlet being installable and invocable inside a plugin context raises a few questions.

- [ ] **pypi publish** (on existing TODO). Gauntlet needs to be `pip install gauntlet` / `uv add gauntlet` reliably so the consumer plugin's setup can pull it.
- [ ] **Homebrew formula** (on existing TODO). For users who prefer a binary install.
- [ ] **Docker image on Docker Hub** (on existing TODO). For consumers who want to run Gauntlet inside an agentcontainer or a CI runner without a Python install.
- [ ] **Claude Code plugin wrapper for Gauntlet itself.** A thin plugin that exposes `/gauntlet <url>` as a slash command, wraps the CLI, and surfaces risk-report fields in a TUI. Not essential for the dark-factory consumer — it'll invoke Gauntlet directly — but a standalone Gauntlet plugin would make the gap between "standalone use" and "embedded use" smaller.
- [ ] **MCP server for Gauntlet.** Consumer uses MCP heavily. A Gauntlet MCP surface (`mcp__gauntlet__run`, `mcp__gauntlet__validate`, `mcp__gauntlet__read_artifacts`) would let an orchestrator drive Gauntlet without subprocess boundaries. Lower priority than the CLI contract; the CLI is the integration point for v0.

## 7. Arsenal curation for platform builds

The consumer targets "platforms" (Slack-like, TODO list, Discord). Arsenals curated for that shape make the hardening loop fast out of the box.

- [ ] **`crud-platform` arsenal.** Authz (users can't read/write each other's resources), input validation, ownership transfer, soft vs hard delete, pagination leakage, filter-bypass. Mirrors the three seeded flaws in the in-memory demo.
- [ ] **`auth-platform` arsenal.** Session fixation, logout correctness, password-reset token reuse, MFA bypass, oauth callback misuse. Broader than the current OWASP subset.
- [ ] **`realtime-platform` arsenal.** WebSocket / SSE authz. Gauntlet doesn't test these today; this item is gated on §3 (WebDriver / CLI adapter work) or on adding a WebSocket adapter.
- [ ] **Arsenal publishing / sharing mechanism.** Today arsenals live in-repo. If the dark-factory ecosystem wants to share arsenals across projects, we need a registry (could be as simple as a `gauntlet arsenal pull gauntlet/auth-platform@v1` that fetches from a curated Git repo).

## 8. Iteration ladder configurability

Already on TODO-v3 item 2. Surfaced again by the consumer (their architecture §5 Divergence). Lifting here for priority.

- [ ] **Configurable iteration stages.** Replace the fixed 4-step ladder with a user-configured sequence. Default stays the current ladder for backward compatibility. Consumer exposes the knob to the spec author so long-running overnight builds can use a deeper ladder and quick-demo builds can use a shallow one.
- [ ] **Iteration-stage budget.** `--iteration-budget "2 baseline, 3 adversarial_misuse"` or similar. Gives the consumer cost control.

## 9. Voice / doc consistency

The sibling project uses a strict writing-voice guide (no em-dashes, decision-first prose, short independent clauses). Gauntlet's docs mostly ignore those rules today. Not urgent, but if both projects eventually ship under one author's name, consistency matters.

- [ ] **Em-dash audit.** Replace `—` with ` - ` (hyphen with spaces) across `README.md`, `docs/*.md`, `CONTRIBUTING.md`. The rationale: em-dashes are an AI tell in Kai's writing-voice doc; Gauntlet is Kai's repo.
- [ ] **Lexical audit.** "utilize" → "use", "sufficient" → "enough", "regarding" → "about", etc. Low-priority, grep-driven.
- [ ] **Decision-first restructure.** Several sections (`README.md` "Core Model", `README.md` "What Makes This Different") bury the decision mid-paragraph. Lift to sentence one.

## 10. Anti-leakage tooling (speculative, longer-horizon)

The consumer's whole value proposition rides on the train/test split holding. If it silently breaks — Planner's weapon files end up in Worker's context, blockers leak into task descriptions — the hardening loop degrades into a fancier version of "the agent grades its own homework." Gauntlet could offer defensive tooling even though the split is ultimately the orchestrator's responsibility.

- [ ] **Blocker-leakage detector.** Given a commit / diff from the Worker and the current weapon set, compute a similarity score between blocker text and commit content. High score = suspicious. Ships as `gauntlet detect-leakage --weapons .gauntlet/weapons --diff <patch>`. Orchestrators can run this as a gate between Worker output and deploy.
- [ ] **Audit-mode risk report.** A flag that makes Gauntlet's risk report explicitly note whether `confirmed_failures` phrasings appear verbatim in the codebase. Signals reward-hacking attempts.
- [ ] **Holdout-integrity CI check.** A test-layer tool that refuses to run Gauntlet if the weapon files are readable from the CWD the Worker was invoked from (enforces the filesystem-level split at the orchestrator layer). Really a consumer-side tool, but the detection logic probably lives here.

---

## Sequencing

Natural order for a team / quarter with Gauntlet and the consumer in parallel:

1. **Now → next few weeks.** §1 (subprocess contract) and §2 (provider-diversity config surface). These unblock the consumer's v1 without asking us for a favor each time. Cheap, high-leverage.
2. **Next quarter.** §4 (Weapon authoring ergonomics) and §5 (integration-test the consumer contract). These harden the integration against drift once the consumer actually starts invoking us.
3. **Later.** §3 (non-HTTP adapters), §7 (arsenal curation), §8 (iteration configurability). These widen Gauntlet's platform coverage; they're real work but they're not blockers for the HTTP-only v0.
4. **Opportunistic.** §6 (plugin ergonomics), §9 (voice), §10 (anti-leakage). Low-urgency, mostly polish or speculative.

## Items deliberately not added here

- A repo-level cross-link from `README.md` to the sibling project. The sibling is in stealth; the phrase "dark factory" already appears in `README.md` as a category concept, which is fine, but the repo itself shouldn't be named here.
- Any restructure of Gauntlet's core roles (Attacker, Inspector, Drone, etc.) to match the sibling's extended roles (Orchestrator, Planner, Worker, Runner). Those are orchestrator-layer roles and the sibling's glossary already maps Attacker → Adversary for their own vocabulary. We shouldn't import their role names.
- Making Gauntlet aware of its consumer. The current posture — Gauntlet is a standalone quality gate that happens to be invoked by an orchestrator — is the right abstraction. Don't couple Gauntlet to a specific orchestrator's shape.
