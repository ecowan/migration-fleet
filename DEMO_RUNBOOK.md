# Demo Runbook — FDE Onsite

Format: **30-min demo · 15-min Q&A · 15-min live extension.**

## The one-sentence pitch
"I turned a 100+ repo Python modernization campaign — the kind I'm running by hand
today — into an orchestrated fleet of Cursor Cloud Agents that migrate each repo
in dependency order, verify their own work, and open reviewable PRs."

## Positioning (say this framing, avoid the other)
Frame it as a **modernization/migration campaign engine**, NOT "dependency
management" — the second framing invites a Dependabot comparison you don't want.
The hero capabilities are the ones dep bots don't have: structural re-tooling
(template + build system + runtime), an opinionated encoded playbook, and
**cross-repo dependency-ordered scheduling**.

## Before the session (de-risking — do this the day before)
1. Seed the three synthetic repos to GitHub; register the custom environment.
2. **Pre-run the fleet live once** so real PRs + artifacts already exist. Keep the
   PR tabs open. Screen-record the run as a fallback in case wifi/API misbehaves.
3. Do the optional prep call — ask about environment setup and any REST rate limits.
4. Rehearse the 30-min flow twice end-to-end, out loud, with a timer.

## 30-minute demo script

**(0–4 min) Problem framing — make it land for engineers who haven't lived it.**
Open with this (the audience is all engineers, but may not share your migration
context — this makes the pain legible in ~45 seconds):

> "Quick context so this lands even if you haven't lived it: picture 120 Python
> services, each set up by a different team at a different time — different build
> scripts, different dependency setups, all pinned to an old Python. Leadership
> says: standardize them all onto one template and toolchain and get them to 3.14.
> That's not one hard problem — it's the same medium problem 120 times, each repo
> subtly different, and some import each other so order matters. By hand it's a
> person-quarter of error-prone toil. That's what I automated."

Then ground it: "This is a real migration I'm running at a regulated financial org
(sanitized here). I did it by hand with an interactive agent per repo — works,
doesn't scale, isn't auditable."
- State the enterprise constraints out loud: audit, human review, no business-logic
  drift, isolation. This is the FDE signal — you understand the customer's world.

**(4–10 min) The approach + architecture.**
- Show the architecture diagram. Emphasize the two-layer design: a bulletproof
  single-repo migration loop, wrapped in a fan-out orchestrator.
- Call out the opinionated choices: `uv` over pip-tools, `just` over make, copier
  for structure, and — the important one — **verification-gated PRs**. "The gate
  is the difference between 'an agent changed files' and 'a migration you can merge.'"

**(10–22 min) Live demo.**
- Show a target repo's "before" state: Makefile, `requirements.txt`, `utcnow()`,
  pydantic v1, `.python-version` = 3.11. Run `pytest` — green.
- **Show the dependency ordering first.** The scheduler prints the waves:
  `common-utils` (shared lib) → `risk-scoring` + `notifications-svc` → `payments-ledger`.
  "The matrix skill computes who imports whom; the shared library migrates and
  goes green before anything that depends on it. This is the fleet-level piece a
  per-repo bot structurally cannot do."
- Kick off the orchestrator. Narrate wave-by-wave execution, parallel within a wave.
- While it runs, open a **pre-baked** PR: show the diff (justfile, `pyproject`,
  `uv.lock`, `utcnow`→`now(UTC)`, pydantic v2 — the *structural* changes, not a bump),
  and the agent's test-green artifact.
- Land on the **dashboard**. Two hero moments:
  1. `risk-scoring` → NEEDS_REVIEW: "it didn't fake success — it couldn't resolve a
     transitive pin, so it flagged it. At 100 repos that triage IS the product."
  2. `payments-ledger` → BLOCKED: "and because payments-ledger imports risk-scoring,
     the engine *refused* to migrate it against an unclean dependency. That safety
     is the difference between a fleet campaign and 100 independent PRs."

**(22–30 min) Trade-offs, limitations, roadmap.**
- Trade-offs: Python + REST API (met the customer in their stack) vs the richer TS
  SDK (gave up streaming/subagents). Polling vs webhooks. Per-repo isolation vs a
  shared cross-repo change.
- Limitations: no cross-repo dependency ordering yet; gates are coarse; cost scales
  with fleet size; template reconciliation is the riskiest step.
- Roadmap: webhook-driven instead of polling; a dependency-graph scheduler so
  shared libs migrate before their consumers; richer policy gates; a
  merge-queue integration.

## Q&A — anticipated questions + strong answers

**"How is this different from Dependabot / Renovate — they do AI breaking-change
fixes now too?"** (Know this: as of April 2026 Dependabot uses Copilot to fix
breaking changes and alerts are assignable to AI agents. Don't pretend otherwise.)
"Those are dependency tools — they keep versions current in steady state, per repo.
This isn't a dependency tool; it's a fleet modernization engine. It re-tools the
repo — imposes our template, swaps the build system, moves the runtime — and
coordinates that across a hundred repos with dependency-ordered scheduling and
triage. The version bump is one step in a playbook, not the product. When the
customer's next campaign is 'rebuild every service on API v2,' the same engine runs
it — Dependabot can't, and neither can a single interactive agent."

**"Why not just script this with sed/codemods?"** Mechanical rewrites (make→just,
pin bumps) are scriptable, yes — and I'd script the deterministic parts. But
dependency resolution and fixing what an upgrade *breaks* (pydantic v1→v2 call
sites, deprecated stdlib) is judgment work that varies per repo. The agent handles
the variable part; the gates keep it honest.

**"How do you trust the output?"** I don't — I gate it and a human merges. PRs, not
pushes. Tests must be green without being weakened. Unresolved work is surfaced,
not hidden. Auditability (who/what/when) via the PR + agent artifacts.

**"What happens when it gets it wrong?"** Three containment layers: per-repo
isolation (one failure can't touch the fleet), the NEEDS_REVIEW class (honest
partial results), and human review before merge. Nothing is irreversible.

**"How does this scale to 100+?"** The concurrency knob and bounded fan-out. The
real limits are API rate/credits and review bandwidth — which is why triage
(done vs needs-review) matters more than raw throughput.

**"Why the REST API and not the SDK?"** The SDK is TS-first and richer. I chose
Python against the language-agnostic REST API because the customer lives in Python
and I wanted the tooling native to their stack. If they were a TS shop I'd flip.

## Live extension — YOU propose it (per FDE intel)

You can propose the extension yourself. So don't wait for a curveball — walk in
with ONE rehearsed extension you'll drive, and a backup. Always narrate the seam
first: "that's a new gate / a playbook change / a concurrency change — here's
where it plugs in."

### PRIMARY OPTION A — coach a stuck agent with a follow-up run (v1)
Reference + code: `extensions/repair_followup.py`. Tested — one follow-up run turns
the needs-review repo green. This is the strongest match for "extend from a
conversation," and it shows off the v1 durable-agent model (workspace persists
across runs). Say: "triage doesn't have to end at needs-review — the v1 API lets me
send the stuck agent one coaching follow-up in the same workspace." Then call
`client.followup(agent_id, PYDANTIC_FIX)` and re-check → green.

### PRIMARY OPTION B — add a compliance policy gate
Reference + exact code: `extensions/policy_gate.py`. It's tested — wiring it makes
`risk-scoring` fail a *security* gate too.

Say: "In a regulated shop, 'tests pass' isn't the bar — compliance wants
guarantees. The gate abstraction lets me encode an org policy as code that applies
across all 120 repos at once. Watch — I add one gate that fails any repo still
carrying a known-CVE dependency."

Do (live, in `orchestrator/gates.py`):
```python
def no_known_cves_gate(poll):
    cves = (poll.get("raw") or {}).get("known_cves") or []
    return CheckResult("no_known_cves", not cves,
                       "; ".join(cves) if cves else "no known CVEs")

DEFAULT_GATES.append(no_known_cves_gate)   # at the bottom of the file
```
Then `python run.py --dry-run` — new column across the fleet; risk-scoring lights
up red on security. Land it: "one function, and every repo in the fleet is now
checked against that policy. That's the leverage."

### BACKUPS (in case the conversation wants more, or steers)
- **Staged/canary rollout** — migrate one repo per wave first, verify, then release
  the rest. Bigger enterprise-rollout story; talk it through, stub the wave split.
- **Cycle handling** (already built) — add `depends_on` edges that form a cycle in
  `repos.yaml`, re-run, show the scheduler flag it for a human instead of hanging.
- **Advisory blocking** — flip `block_on_upstream: false`; `payments-ledger`
  migrates anyway with a warning. One-line policy change; discuss safety vs throughput.
- **Playbook step** — add a step (CODEOWNERS, SECURITY.md) to the markdown playbook
  to show behavior is data, not code.

If they *do* steer somewhere you didn't rehearse: narrate the seam, think out loud,
and it's fine to say "let me stub the shape and talk through the rest" — a clear
plan beats a rushed half-implementation.

## If the live agent run stalls
Switch to the pre-baked PRs and the recording. Say it plainly: "cloud agents take
a few minutes and cost credits, so for a live audience I pre-ran the fleet — here's
the real output." Composure > a spinning agent.
