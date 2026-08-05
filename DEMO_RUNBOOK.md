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

> **Live-only.** Nothing is pre-baked — no recording, no PR from a previous run, no
> dry-run dashboard. Every artifact shown is produced by the run the room watches.
> That raises the bar on preparation considerably; see "Expected on-screen state"
> in `PRESENTATION_SCRIPT.md` for the failure modes.

1. Seed the synthetic repos to GitHub with `.cursor/Dockerfile` +
   `.cursor/environment.json` on `main` (template under `environment/`).
2. **Make `risk-scoring` genuinely un-migratable** (a real unresolvable transitive
   pin) so the NEEDS_REVIEW outcome is a property of the repo, not a dry-run
   injection. `flaky_repo` in `repos.yaml` does nothing on a live run.
3. **Rehearse live end-to-end at least twice**, with a timer, resetting state
   between runs (close PRs, delete branches + `0.0.1.dev0` tags, remove
   `fleet_report.html`). Confirm the mix is 2 DONE · 1 NEEDS_REVIEW · 1 BLOCKED
   both times, and record the real wall-clock time at which wave one's PR appears —
   §8's back half is anchored to it.
4. Do the optional prep call — ask about environment setup and any REST rate limits.
5. Confirm credits for a full run plus a retry, and that `CURSOR_API_KEY` +
   `GITHUB_TOKEN` are live in the shell you'll present from.

## 30-minute demo script

> **`PRESENTATION_SCRIPT.md` is the authoritative beat-by-beat script** (11 sections,
> with the timing cheat-sheet and the expected on-screen state). What follows is the
> condensed version — keep this doc for the positioning, Q&A, and extension material.

**(0–4 min) Problem framing — make it land for engineers who haven't lived it.**
Open with this (the audience is all engineers, but may not share your migration
context — this makes the pain legible in ~45 seconds):

> "Quick context so this lands even if you haven't lived it: picture 120 Python
> services, each set up by a different team at a different time — different build
> scripts, different dependency setups, all pinned to an old Python. Leadership
> says: standardize them all onto one template and toolchain and get them to 3.14.
> The difficulty is in the repetition: the same medium problem 120 times, each repo
> subtly different, and some import each other so order matters. By hand it's a
> person-quarter of error-prone toil. That's what I automated."

Then ground it: "This is a real migration I'm running at a regulated financial org
(sanitized here). I did it by hand with an interactive agent per repo — works,
doesn't scale, isn't auditable."
- State the enterprise constraints out loud: audit, human review, no business-logic
  drift, isolation. This is the FDE signal — you understand the customer's world.

**(4–10 min) The approach + architecture.**
- **No slides — drive this from the editor.** Open `repos.yaml`,
  `playbook/migration_playbook.md`, the `orchestrator/` tree, then `gates.py`.
  Emphasize the two-layer design: a bulletproof single-repo migration loop,
  wrapped in a fan-out orchestrator. (§7 of `PRESENTATION_SCRIPT.md` has the
  beat-by-beat version.)
- Call out the opinionated choices: `uv` over pip-tools, `just` over make, copier
  for structure, and — the important one — **verification-gated PRs**. "The gate
  is the difference between 'an agent changed files' and 'a migration you can merge.'"

**(10–22 min) Live demo.**
- Show `risk-scoring`'s "before" state: Makefile, `requirements.txt` hard-pinned to
  `pydantic==1.10.2`, `.python-version` = 3.11 — then **follow the import** from
  `src/risk/score.py` into `common-utils/src/common/money.py`, which is where
  `utcnow()` and the pydantic v1 `class Config` actually live. That import trail is
  the concrete case for wave ordering. Then `just test-one risk-scoring` — green,
  with deprecation warnings already in the output nobody reads.
  (**Not** a bare `pytest`/`python -m pytest`: there's no bare `python` on the Mac.)
- **Show the dependency ordering first.** The scheduler prints the waves:
  `common-utils` (shared lib) → `risk-scoring` + `notifications-svc` → `payments-ledger`.
  "We scan each checkout's requirements and imports to build the dep matrix;
  the shared library migrates and goes green before anything that depends on it.
  This is the fleet-level piece a per-repo bot structurally cannot do."
- Kick off the orchestrator live (`just run --live --verbose`). Narrate wave-by-wave
  execution, parallel within a wave.
- **While it runs — no PR exists yet.** Cover the gap with the "why not a codemod"
  rebuttal (against the `class Config` you already showed) and `just map`. Then,
  once wave one lands, **refresh the PR list live** and open the PR the agent just
  created: the diff (justfile, `pyproject`, `uv.lock`, `utcnow`→`now(UTC)`,
  pydantic v2 — the *structural* changes, not a bump) and the test-green artifact.
  "This didn't exist when I started talking" is the line the whole demo builds to.
- Land on the **dashboard**. Expected mix: **2 DONE · 1 NEEDS_REVIEW · 1 BLOCKED.**
  Three hero moments:
  1. `notifications-svc` → DONE with `PASS:upstream_pins`: "it consumed
     `common-utils@0.0.1.dev0` the shared library published one wave earlier —
     that's the fleet staying coherent mid-campaign."
  2. `risk-scoring` → NEEDS_REVIEW: "it couldn't resolve a transitive pin, so it
     flagged itself and stopped. And look: it *passed* `upstream_pins` — the
     coordination worked exactly as designed; it pinned its upstream correctly and
     then got stuck on its own dependency. At 100 repos that precision IS the product."
  3. `payments-ledger` → BLOCKED: "and because payments-ledger imports risk-scoring,
     the engine *refused* to migrate it against an unclean dependency. That safety
     is the difference between a fleet campaign and 100 independent PRs."
- Point at the **cost receipt**: per-bucket token spend from `GET /v1/agents/{id}/usage`,
  ~$1.13–$2.15 this run, projected $45–$86 across 120 repos. The range is honest —
  Cursor doesn't publish a cache-write rate for composer-2.5, so it carries the
  uncertainty instead of inventing a number.

**(22–30 min) Trade-offs, limitations, roadmap.**
- Trade-offs: Python + REST API (met the customer in their stack) vs the richer TS
  SDK (gave up streaming/subagents). Polling vs webhooks. Per-repo isolation vs a
  shared cross-repo change. Blocking consumers on unclean deps — safety over
  throughput, on purpose.
- Limitations: gates are coarse ("tests green" is a real signal that stops short of
  a proof); polling costs latency webhooks would save; template reconciliation is
  the riskiest step; cost scales linearly with fleet size — which is why triage
  matters more than raw throughput.
- Roadmap: webhook-driven instead of polling; richer policy + supply-chain gates;
  merge-queue integration; and the same engine running the *next* campaign
  ("rebuild every service on API v2") — a different playbook, same orchestrator.

> Dependency-ordered scheduling is **built**, not roadmap — `dep_matrix.py` derives
> the graph from repo contents and `scheduler.py` topo-sorts it into waves. Don't
> list it as a gap; it's the differentiator.

## Extension questions — the two the team is most likely to push on

These are conversation questions, not demo beats. Both reward specificity: the
credible answer names the actual breaking point in the actual code, or the actual
number. Know these cold; you won't have the editor open.

---

### "If you rolled this out to more teams / a bigger fleet, what breaks first?"

**Open by separating the two axes** — they fail in completely different places, and
saying so up front is most of the answer:

> "Two different questions hiding in that one. More *repos* is mostly a
> throughput and durability problem, and it's the easier one. More *teams* is a
> multi-tenancy problem, and that's where the current design actually falls over.
> Let me take them in the order I'd hit them."

**Axis 1 — more repos in one fleet. Four things break, in this order:**

1. **The dependency scan needs local checkouts.** `dep_matrix.py` resolves every
   repo to `targets/<name>/` on local disk and parses its AST. At 4 repos that's
   free; at 200 it means cloning and refreshing 200 working copies before the run
   starts. **This breaks first and it's the one I'd fix first** — move the scan
   into a cheap per-repo agent, or read from the host's API instead of a checkout.
2. **There's no persistence.** The whole run is in-memory `results: dict` inside
   one `asyncio` process (`orchestrator.py`). No checkpoint, no resume. A 4-repo
   run takes 5 minutes so nobody notices; a 200-repo run takes hours, and if it
   dies in wave 6 of 9 you have lost the state and re-spend the credits. Needs a
   durable run record before it's real.
3. **The wave barrier gets expensive.** `await asyncio.gather(...)` per wave means
   the whole wave waits on its slowest repo. At 4 repos, invisible. At a wave of
   60, one gnarly monolith holds 59 finished agents hostage. The fix is to release
   each repo as soon as *its own* upstreams are clean, rather than at the wave
   boundary — the graph already has enough information, the executor just doesn't
   use it.
4. **Rate limits and no budget ceiling.** It polls (`max_polls=300` per repo) and
   spend is recorded after the fact. There's no per-run cap and no circuit
   breaker, so a misconfigured campaign burns credits until someone hits Ctrl-C.

**Axis 2 — more teams. This is the harder one, and it's where I'd want design
input rather than just engineering:**

1. **Gates are global.** `DEFAULT_GATES` is one module-level list — every repo in
   the fleet gets identical policy. The moment two teams want different bars
   (payments wants a CVE gate, the data team doesn't), that list has to become
   per-repo or per-team config. The abstraction is right; the *binding* is wrong.
2. **One manifest, one set of knobs.** `repos.yaml` holds a single model, a single
   `concurrency`, a single pricing tier, a single `block_on_upstream` policy. Teams
   need their own concurrency budgets and their own safety posture.
3. **One identity — and this is the actual blocker.** Every agent runs as one
   `CURSOR_API_KEY`. The system has no concept of *which user requested this run*,
   so it can't authorize (any user can migrate any repo in the manifest) and can't
   attribute (the audit trail says "the orchestrator did it"). **For a regulated
   customer that's a compliance blocker, not a nice-to-have** — it's the one thing
   on this list I'd insist on before a second team touched it.
4. **Shared global namespaces.** `FLEET_DEV_VERSION` is a hardcoded `0.0.1.dev0`,
   and `fleet_report.html` / `fleet_usage.json` are fixed paths overwritten every
   run. Two concurrent campaigns collide on both — same tag, clobbered reports.

**Close it with the honest framing** — this is the part that lands:

> "None of that is a rewrite, and I want to be clear about which is which. The
> orchestration core — the graph, the waves, the gates, the classifier — is the
> part I'd keep. What's missing is everything around it: durable state,
> multi-tenancy, and an identity model. That's the difference between a tool I run
> and a platform a hundred engineers run, and it's maybe the more interesting
> problem of the two."

**If pushed on "so how long?"** — resist a number. Say the shape: "durable state
and per-team config are well-understood work. The identity and authorization model
is the one I'd want to design with your security people before writing code,
because getting it wrong in a regulated shop is expensive."

---

### "Sell this to a VP of Engineering."

**Do not lead with the architecture.** A VP doesn't buy topological sorting. Lead
with the thing they feel every quarter:

> "Right now this class of work is unschedulable. It never wins against a feature,
> so it waits until a CVE drops or two libraries stop speaking — and then it's an
> emergency that rips a team off the roadmap for a quarter, at the worst possible
> time. I'm not selling you faster migrations. I'm selling you migrations that go
> on the calendar."

**Then the three numbers.** Have these ready and be precise about what they cover:

| | by hand | with the fleet |
|---|---|---|
| Engineer time | ~a person-quarter of toil | ~1 engineer-week of PR review (120 PRs × ~20 min) |
| Compute | — | ~$45–$86 for the fleet |
| What you get back | a migration | a migration **plus** an audit trail and cost-per-outcome |

**The honest qualifier, which you should volunteer** — it makes the rest credible:

> "That review week is real and it doesn't go away. What changes is what the
> engineer is doing during it: judging finished work instead of doing the work.
> And the triage means they're not judging all 120 equally — roughly 100 are
> routine approvals and 20 are flagged as actually needing thought."

**Then the two arguments that outlast this migration:**

1. **It's a platform bet, not a project.** This campaign is "get to 3.14." The
   next is "rebuild every service on API v2," or "respond to the next log4j in
   days instead of months." Same engine, different playbook file. The second
   campaign is nearly free, and that's where the ROI actually compounds.
2. **It answers the budget question they can't answer today.** The org spends $2M
   a year on engineering tooling with almost no visibility into output per dollar.
   This produces spend-per-outcome per run, itemized. "For the first time you can
   say what a migration costs, before you approve it."

**Anticipate their four objections:**

- *"What if it breaks production?"* → Nothing auto-merges. Every result is a PR a
  human approves, gates run before it's even proposed, and each repo is isolated
  so one bad migration can't touch the fleet.
- *"What's the maintenance cost of the thing itself?"* → Be straight: it's ~1k
  lines of Python and it will need an owner. The honest pitch is that it's far
  cheaper to maintain than the recurring emergency it replaces, and the playbook —
  the part that changes per campaign — is markdown, so it doesn't need an engineer
  to change behavior.
- *"Does this lock us into one vendor?"* → The orchestration logic is ours. The
  agent is a swappable worker behind a REST call, and self-hosted worker pools mean
  the code can stay on our infra.
- *"Why not just hire contractors for a quarter?"* → Contractors leave with the
  context. This leaves behind a versioned playbook, a repeatable engine, and a
  record of what was done to every repo — and it's there when the next campaign
  starts.

**Land it in one sentence** if you only get one:

> "It turns an unbudgetable emergency into a line item with a known cost and an
> audit trail."

---

## Q&A — anticipated questions + strong answers

**"How is this different from Dependabot / Renovate — they do AI breaking-change
fixes now too?"** (Know this: as of April 2026 Dependabot uses Copilot to fix
breaking changes and alerts are assignable to AI agents. Don't pretend otherwise.)
"Those are dependency tools — they keep versions current in steady state, per repo.
What I built is a fleet modernization engine. It re-tools the repo — imposes our
template, swaps the build system, moves the runtime — and coordinates that across a
hundred repos with dependency-ordered scheduling and triage. The version bump is
one step in a playbook of twelve. When the customer's next campaign is 'rebuild
every service on API v2,' the same engine runs it — Dependabot can't, and neither
can a single interactive agent."

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

Say: "In a regulated shop, compliance wants guarantees on top of 'tests pass.' The
gate abstraction lets me encode an org policy as code that applies across all 120
repos at once. Watch — I add one gate that fails any repo still carrying a
known-CVE dependency."

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

There is no pre-baked fallback — that's the deliberate trade of going live-only.
In order: name it without apology ("real VMs, sometimes one takes a bad five
minutes"), spend the time on `just map` (read-only, always works), then **pull Q&A
forward** — you have 15 min budgeted after this and borrowing five early beats
dead air. If it's truly dead, stop it, say so, and use the last rehearsal's
`fleet_report.html` for §9 *while stating out loud that it's from a rehearsal run*.
Never restart the fleet mid-demo. Full version in §8's FALLBACK block.

Composure > a spinning agent. And honesty about provenance costs far less than a
discovered pre-baked artifact.
