# Hardening prompt template

A replayable prompt for asking a Claude Code host to run a service through Gauntlet, fix what it finds, and gate on a clean clearance. Fill the `{{...}}` slots, paste into a fresh session, and let it run.

This template is the user-facing *outer* prompt. It tells the host how to set up Gauntlet, where to find the trials, how to act on findings, and what safety constraints apply. The actual adversarial loop is driven by the `gauntlet` skill that the plugin installs, not by anything in this file.

## When to use this

Use it when you want to put a service you own through adversarial testing. Authorized testing of your own infrastructure only. If you don't own the SUT, stop and get written authorization first.

## The template

Copy everything between the fences. Replace each `{{SLOT}}`. Drop sections that don't apply (auth, downstream-safety) rather than leaving them empty.

````
Harden {{SERVICE_URL}} using gauntlet (https://github.com/coilysiren/gauntlet).

I own both the SUT and the repo under test; this is authorized adversarial
testing of my own infrastructure.

Setup:
1. If the gauntlet skill is not available in this session, install the plugin:
     claude plugin install https://github.com/coilysiren/gauntlet
   Then confirm with /mcp (server "gauntlet") and /agents (gauntlet-attacker,
   gauntlet-inspector, gauntlet-holdout-evaluator). If the plugin can't load
   in this harness, stop and tell me which harness you're in so I can switch
   to one that can.

2. If {{REPO_PATH}}/.gauntlet/trials/ does not exist or is empty, author
   trials first via the gauntlet-author skill ("author trials from this
   spec"). The spec is the surface described below. Save trials to
   {{REPO_PATH}}/.gauntlet/trials/ and commit them ("add: gauntlet trials
   for {{REPO_NAME}}") before running the loop.

   Attack surface to cover, at minimum:
   {{ATTACK_SURFACE}}

3. Run the gauntlet loop against {{SERVICE_URL}}. Use the skill's standard
   4-iteration ladder per trial.

   Auth: {{AUTH_HEADERS_OR_NONE}}

4. For each medium-risk finding: fix in {{REPO_NAME}}, commit (one logical
   fix per commit), push, wait for the deploy ({{DEPLOY_PIPELINE}}), then
   replay the finding with replay_finding to confirm. Repeat until the loop
   returns low risk for that trial.

5. For any high-risk finding: stop the automated loop, summarize the
   finding, and surface to me. Do not auto-fix high-risk issues. The
   skill's own guidance treats high risk as a human-review gate.

6. End state: assemble_final_clearance returns pass across all trials,
   .gauntlet/trials/ is committed, all fixes are on main and deployed.

Safety constraints:
- Run gauntlet only against {{SERVICE_URL}} or a localhost copy. {{DOWNSTREAM_SAFETY}}
- Follow workspace conventions in ~/projects/coilysiren/coilyco-ai/AGENTS.md:
  no em-dashes, she/her pronouns, commit-per-coherent-unit cadence, push
  after each commit, no PRs unless I ask.
````

## Slot guide

| Slot | What goes in it | Example |
|---|---|---|
| `{{SERVICE_URL}}` | Full URL of the running SUT | `https://eco-mcp.coilysiren.me/` |
| `{{REPO_PATH}}` | Absolute path to the repo whose code backs the SUT | `~/projects/coilysiren/eco-mcp-app` |
| `{{REPO_NAME}}` | Short repo name for commit messages | `eco-mcp-app` |
| `{{ATTACK_SURFACE}}` | Bulleted list of routes / params / behaviors worth probing. The author skill turns this into trials, so be specific. See guidance below. | (multi-line, see example) |
| `{{AUTH_HEADERS_OR_NONE}}` | Either `none, the service is unauthenticated` or a description of how to obtain headers (do not paste tokens; point at an env var or SSM path) | `none, the service is unauthenticated` |
| `{{DEPLOY_PIPELINE}}` | One sentence on what "push, wait for deploy" means here | `push to main, GH Actions builds + rolls out to k3s, ~3 min` |
| `{{DOWNSTREAM_SAFETY}}` | Anything the SUT talks to that must NOT be attacked. Drop the line if there are no downstreams. | `The trial set may name third-party servers as legitimate downstream targets; do not attack those, only my service's handling of them.` |

## Filling in the attack surface

The bullet list under `{{ATTACK_SURFACE}}` is the most important slot. It's what the gauntlet-author skill reads to invent trials, and trials are the only thing the loop actually tests against. Vague surface in, weak trials out.

Aim for 5 to 10 bullets, each naming a concrete piece of the SUT and a class of attack. Read the routing file (Starlette routes, Flask routes, FastAPI routers, whatever) and walk it endpoint by endpoint.

Common categories worth a bullet each when present:

- **User-controlled URLs that the server fetches.** SSRF: cloud metadata (169.254.169.254), localhost, RFC1918, `file://`, `gopher://`, redirect chains, DNS rebinding, oversized responses, slow responses (slowloris).
- **Path parameters that route into dispatch.** Unknown values, control characters, very long values, values that collide with framework internals.
- **Query/body params passed unvalidated to a downstream call.** Unexpected keys, oversized values, type confusion (arrays via repeated keys), nested injection, encoding tricks.
- **Streaming or stateful endpoints.** Malformed protocol frames, oversized payloads, content-type confusion, method spoofing, half-open connections.
- **Resource exhaustion.** Concurrent requests that hold open upstream connections, unbounded response sizes, no concurrency cap, no timeout.
- **CORS / auth boundary.** Open CORS, missing auth on routes that look unprotected by accident, IDOR-style cross-tenant access if the service is multi-tenant.

If a category doesn't apply (no user-controlled URLs, no auth, etc.), drop it. Don't invent surface that isn't there.

## Worked example: eco-mcp-app

This is the filled-in version Kai actually replays for `eco-mcp-app`. Useful as a reference for what a real `{{ATTACK_SURFACE}}` slot looks like.

````
Harden https://eco-mcp.coilysiren.me/ using gauntlet (https://github.com/coilysiren/gauntlet).

I own both the SUT and the repo under test; this is authorized adversarial
testing of my own infrastructure.

Setup:
1. If the gauntlet skill is not available in this session, install the plugin:
     claude plugin install https://github.com/coilysiren/gauntlet
   Then confirm with /mcp and /agents. If the plugin can't load in this
   harness, stop and tell me which harness you're in.

2. If ~/projects/coilysiren/eco-mcp-app/.gauntlet/trials/ does not exist or
   is empty, author trials first via the gauntlet-author skill. Save trials
   there and commit them ("add: gauntlet trials for eco-mcp-app") before
   running the loop.

   Attack surface to cover, at minimum:
   - ?server= argument on /preview, /preview-map, and every MCP tool that
     accepts it. The handler builds an outbound httpx request to whatever
     host:port the caller supplies. SSRF candidates: 169.254.169.254,
     localhost services, RFC1918, file://, gopher://, redirect chains,
     DNS rebinding, oversized responses, slowloris.
   - /preview/{tool} path parameter is an arbitrary tool name. Unknown
     names, names with control chars, very long names, names that collide
     with Starlette internals.
   - Query params on /preview/{tool} flow straight into tool arguments as
     a dict with no shape validation. Unexpected keys, oversized values,
     type confusion via repeated keys, nested injection.
   - /mcp/ Streamable-HTTP endpoint, stateless, CORS open. Malformed
     JSON-RPC, oversized payloads, content-type confusion, method spoofing.
   - Resource exhaustion: concurrent /preview hits while upstream Eco
     server is slow. Timeout is 5s but no visible concurrency cap.

3. Run the loop against https://eco-mcp.coilysiren.me/. Standard 4-iteration
   ladder.

   Auth: none, the service is unauthenticated.

4. Medium-risk findings: fix in eco-mcp-app, commit per logical fix, push,
   wait for the deploy (push to main, GH Actions builds + rolls out to k3s,
   ~3 min), replay to confirm.

5. High-risk findings: stop, summarize, surface to me.

6. End state: assemble_final_clearance returns pass.

Safety constraints:
- Run gauntlet only against https://eco-mcp.coilysiren.me/ or a localhost
  copy. Trial sets may name other public Eco servers (AWLGaming,
  GreenLeaf, etc.) as legitimate downstream targets the SUT proxies to;
  do not attack those, only my service's handling of them.
- Follow workspace conventions in ~/projects/coilysiren/coilyco-ai/AGENTS.md.
````
