# 30-Minute Demo — Step-by-Step Script

Format reminder: **30-min demo** (this doc) → 15-min Q&A → 15-min live extension
(you propose it). Audience: all engineers, may not share your migration context.

Each block: **SHOW** (what's on screen) · **SAY** (key lines) · **PROVES** (what it
scores). Times are cumulative; the dashboard at ~24 min is the payoff — protect it.

Pre-flight (before you share screen): editor open on the project · a terminal ·
pre-baked PR tab(s) · the dashboard file · `deck.html` · mic + screen-share tested.

---

## 0:00–3:00 — Frame the problem (make it land for anyone)
**SHOW:** deck slide 1 (problem).
**SAY (the opener, ~45s):** "Quick context so this lands even if you haven't lived
it: picture 120 Python services, each set up by a different team at a different
time — different build scripts, different dependency setups, all pinned to an old
Python. Leadership says standardize them all onto one template and toolchain and
get them to 3.14. That's not one hard problem — it's the same medium problem 120
times, each repo subtly different, and some import each other so order matters. By
hand it's a person-quarter of error-prone, unauditable toil."
Then ground it: "This is a real migration I'm running at a regulated financial org,
sanitized here. I did it by hand with an interactive agent per repo — works,
doesn't scale, isn't auditable. So I built the thing that does."
**PROVES:** enterprise relevance + you can make it legible to a mixed room.

## 3:00–4:00 — The Cursor primitive (1 min)
**SHOW:** talk to the architecture slide as you bring it up.
**SAY:** "A Cursor Cloud Agent is a worker in an isolated VM — it clones a repo,
does the work, opens a PR with artifacts. I drive them programmatically from Python
against the Cloud Agents REST API. The agent is the worker; what I built is the
foreman — the thing that decides what runs, in what order, and whether to trust the
result."
**PROVES:** you understand the platform, and pre-empts "isn't this just the agent?"

## 4:00–8:00 — Architecture (4 min)
**SHOW:** deck slide 2 (the pipeline diagram).
**SAY:** walk left-to-right — inputs (`repos.yaml` + `playbook.md` + the
dependency-matrix skill) → **scheduler** (topo-sorts into waves) → **orchestrator**
(wave-by-wave, bounded concurrency) → a **Cloud Agent per repo** in a custom env
(uv·just·copier·3.14) → **verification gates** → **PRs + dashboard**, human merges.
Emphasize: "Two layers — a bulletproof single-repo migration loop, wrapped in a
fleet orchestrator. And the opinionated calls: uv over pip-tools, just over make,
copier for structure, and verification-gated PRs."
Name the seams out loud: "gates, the playbook, and concurrency are all pluggable —
I'll come back to that." (Foreshadows the extension.)
**PROVES:** non-trivial systems thinking; sets up the extension.

## 8:00–10:00 — The "before" state (2 min)
**SHOW:** open `targets/risk-scoring` — `Makefile`, `requirements.txt`,
`src/risk/score.py` (point at `datetime.utcnow()` and pydantic v1 `class Config`),
`.python-version` = 3.11. Run `pytest` → green.
**SAY:** "Representative of 120 of these. Green today, on old everything. The
upgrade will *break* some of this — `utcnow` is gone, pydantic v1 needs the v2
migration — and fixing what breaks is the judgment work, not a version bump."
**PROVES:** concreteness + honesty about the real difficulty.

## 10:00–12:00 — Dependency ordering (2 min) — the differentiator
**SHOW:** run the orchestrator so the wave plan prints first:
`wave 1: common-utils → wave 2: risk-scoring, notifications-svc → wave 3: payments-ledger`.
**SAY:** "My dependency-matrix skill computes who imports whom. The shared library
migrates and goes green *before* anything that depends on it — you never migrate a
consumer against an un-migrated dependency. This is the fleet-level piece a
per-repo bot structurally can't do."
**PROVES:** the key differentiator; systems thinking.

## 12:00–20:00 — The run + real output (8 min) — the heart
**SHOW:** let the run go (live if you have access; otherwise the pre-baked run).
Narrate wave-by-wave, parallel within a wave. **While it runs**, open a pre-baked
PR (from the day-before run, or the hand-migrated `risk-scoring`): walk the diff —
`justfile`, `pyproject.toml` + `uv.lock`, `utcnow`→`now(UTC)`, pydantic v2 config —
and the test-green artifact.
**SAY:** "This is the structural change, not a bump: the build system, the
dependency tooling, the runtime, and the code the upgrade broke — all in one
reviewable PR."
**PROVES:** it actually works; the transformation is real and non-trivial.
**FALLBACK:** if a live agent stalls, say it plainly — "cloud agents take minutes
and cost credits, so I pre-ran the fleet; here's the real output" — and go to the
pre-baked PRs + recording. Composure over a spinner.

## 20:00–24:00 — The dashboard (4 min) — the hero moment
**SHOW:** open `fleet_report.html`: common-utils DONE · notifications-svc DONE ·
risk-scoring NEEDS_REVIEW · payments-ledger BLOCKED, with the wave strip on top.
**SAY (two beats):**
1. "risk-scoring didn't fake success — it couldn't resolve a transitive pin, so it
   flagged it for a human. At 120 repos, that triage *is* the product."
2. "And payments-ledger imports risk-scoring, so the engine *refused* to migrate it
   against an unclean base — it's blocked, not broken. That safety is the difference
   between a coordinated campaign and 100 independent PRs."
Point at the **cost banner**: "and this is real token usage from the v1 usage
endpoint — spend concentrates in the gnarly repo, and here's the projected cost
across the full 120-repo fleet. Cost governance is part of running this at scale."
**PROVES:** enterprise judgment + trustworthiness + cost awareness + the design.

## 24:00–28:00 — Trade-offs, limits, roadmap (4 min)
**SHOW:** deck slide 5 (trade-offs); slide 4 (landscape) only if pace allows.
**SAY:** Decisions — "Python + REST to meet the customer in their stack; PRs never
auto-merge; block consumers on unclean deps — safety over throughput." Limits —
"polling not webhooks yet; gates are coarse; template reconciliation is the riskiest
step; cost scales with the fleet, which is why triage matters more than raw
throughput." Roadmap — "webhook-driven runs, richer policy/SCA gates, and the same
engine runs the *next* campaign — 'rebuild every service on API v2' — not just this
one." If asked how it differs from Dependabot/Moderne, give the one-liner (see
runbook).
**PROVES:** maturity, honesty, and that you know the landscape.

## 28:00–30:00 — Close + hand off (2 min)
**SAY:** "The through-line: one agent became a fleet, then an *ordered, gated,
triaged* campaign — and every step reused the same clean core, which is what makes
it extend cleanly." Then propose the extension proactively: "One direction I'd love
to show live — encoding an org compliance policy as a gate that applies across all
120 repos at once. Happy to build that now, or take questions first."
**PROVES:** confidence; you drive the extension (per their intel); smooth handoff.

---

## Timing cheat-sheet
| min | block | if you're behind, cut… |
|-----|-------|------------------------|
| 0–3  | Problem framing | keep the opener; trim the grounding |
| 3–4  | Cursor primitive | merge into architecture |
| 4–8  | Architecture | stay high-level; don't read code |
| 8–10 | Before state | one file, one `pytest` |
| 10–12| Dependency ordering | **never cut — the differentiator** |
| 12–20| Run + PR diff | show one PR well, not three |
| 20–24| Dashboard | **never cut — the payoff** |
| 24–28| Trade-offs | drop the landscape slide first |
| 28–30| Close + handoff | keep the extension proposal |

Protect 10–12 and 20–24. Everything else can compress.
