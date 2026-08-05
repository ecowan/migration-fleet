# 30-Minute Demo — Script

Format: **30-min demo** (this doc) → 15-min Q&A → 15-min live extension (you propose it).
Audience: all engineers, may not share your migration context.

Pre-flight (before you share screen): editor open on the project · a terminal ·
pre-baked PR tab(s) · `fleet_report.html` · `deck.html` · mic + screen-share tested.
Run `just demo` once to warm it up and confirm the outcome mix is
**2 DONE · 1 NEEDS_REVIEW · 1 BLOCKED** (see "Expected on-screen state" at the bottom).

---

## 1 · Background [~2 min]
**[Slide: problem]**

Before I show you anything running, I want to put you inside the problem — because
if you haven't worked somewhere like this, it's easy to underestimate.

Picture a financial services company. Hundreds of engineers, offices in five
countries, and a codebase that's been accreting for two decades. Some of these
repos are ten, fifteen, twenty years old. Over that much time, drift sets in
everywhere — every team, every era did things a little differently. Different
Python versions, different build setups, different everything.

The goal sounds simple: make these projects consistent. But it's more than
flipping a version number and running the tests. It's bringing them to a modern
baseline — modern build tooling like uv and just, real CI, a shared copier
template so every repo has the same shape. Now do that across about two hundred
repositories.

## 2 · Business problem [~1.5 min]

Here's the thing about this work: it's always at the bottom of the task list.
None of it is ever "breaking today," so it never wins against a feature or a
customer bug. So it waits — until a critical vulnerability gets published, or two
libraries finally refuse to work together, and suddenly it's an emergency. And
when that happens, it yanks engineers off the work that actually moves the
business — features, real bugs — to go do a migration nobody scheduled.

## 3 · The gap in existing tools [~2 min]

There are tools in this space. Dependabot and friends will open a PR bumping a
version number. That's useful — and it's not what I'm talking about. They won't do
a wholesale migration: they won't restructure the project, add your build tooling,
apply your template. That's the gap.

And there's a second thing they don't touch: order. In a shop like this, repos
depend on each other — lots of internal libraries. You can't just fire two hundred
migrations at once. You have to understand the topology — who imports what — and
migrate from the lowest layers up. Fix the shared library first, get it green,
then the things that depend on it, and so on. Get the order wrong and you're
migrating against a moving target.

## 4 · This is real — how I run it today [~1.5 min]

This isn't hypothetical for me. I've been doing exactly this since March. With
today's AI tools it's already faster than by hand — but it could be far faster,
because of how I'm running it. Right now I'm babysitting: monitoring windows,
managing processes on my own machine, approving each git push, reviewing and
pushing the dev tags by hand. I'm in the loop for every step.

## 5 · Why that falls short — three ways [~3 min]

That creates three problems.

**Scaling.** What I'm doing is really the old "one developer, one task" pattern
with a faster developer. I can only watch so many processes at once — my own
attention is the bottleneck.

**Auditing.** There's no record of what actually happened — the prompt I used, the
back-and-forth with the agent. So when two repos come out differently, I can't
compare them, because the session histories were never saved. And in a regulated
environment, "trust me, I ran an agent" isn't good enough.

**Budget.** This organization spends over two million dollars a year on tooling for
its engineers — development, knowledge sharing, in-house tools — and there's almost
no visibility into output per dollar. We're spending; we can't tell what we're
getting.

## 6 · The shift — a cloud agent system [~2.5 min]

So here's the change I want to make. Instead of me at my machine driving one
migration at a time, you build a cloud agent system once, and run it — periodically,
on demand, across the whole fleet — and you get concrete numbers back: spend per
execution, and outcome per execution.

And this is where running on a cloud platform specifically changes the game —
versus the local-first way I've been working, and versus tools like Claude Code
that run on your own machine:

**Unattended and parallel.** The agents run in the cloud, so I launch a fleet and
walk away. I'm not babysitting windows on a laptop — my attention stops being the
cap on throughput.

**Managed, isolated environments.** Each agent gets its own isolated environment the
platform provisions — it clones the repo and opens the pull request. No local
processes to manage, no pushes to approve by hand.

**Auditability for free.** Every run is recorded through the API — the prompt, the
steps, the artifacts, the outcome. That's the audit trail I don't have today.

**Cost telemetry per run.** Real spend-per-outcome numbers — exactly the visibility
that two-million-dollar budget is missing.

And the question a bank always asks — "does our code leave our infrastructure?" —
has an answer: the platform supports self-hosted worker pools, so agents can run on
our own infra. Cloud economics without giving up control.

Contrast that with local-first. A local agent is powerful, but the moment you have a
fleet, the fleet-level tooling becomes your problem — provisioning machines,
wrangling git worktrees, aggregating logs, capping concurrency yourself. The cloud
platform hands you that layer, so you build the migration logic, not the plumbing.

**[TRANSITION → "Let me show you what that looks like."]**

## 7 · Architecture — the worker and the foreman [~4 min]
**[Slide: pipeline diagram]**

Start with the primitive, because everything else sits on top of it.

A Cursor Cloud Agent is one worker in an isolated VM. It clones a repo, does the
work, opens a pull request. That's the unit. What I built is not that — the agent
is the **worker**, and what I wrote is the **foreman**: it decides what runs, in what
order, and whether to trust what comes back. Roughly a thousand lines of Python
against the Cloud Agents REST API.

**Two inputs.** A manifest — the repo list, the concurrency limit, the pricing.
And a playbook: the migration itself, written as markdown. Adopt the copier
template, convert Make to just, move to uv, get to 3.14, fix what the upgrade
breaks. That playbook is a file in git. It's versioned, reviewed, and diffable —
which is the first half of the auditing answer I said I didn't have. When two repos
come out differently, I can point at exactly which revision of the instructions each
one got.

**The scheduler orders the fleet.** This is the piece I said Dependabot structurally
can't do. It topologically sorts the dependency graph into *waves* — repos in a wave
have no dependency on each other and run in parallel; waves run in sequence. The
shared library migrates and goes green before anything that imports it. And because
import cycles are a real thing in a twenty-year-old codebase — service A and B
importing each other — it detects them and flags them for a human instead of hanging.

**The orchestrator runs each wave.** Bounded fan-out, one agent per repo. It
launches, polls until the agent reaches a terminal state, then runs verification
gates against the result. Concurrency is a single number in the manifest — that's
the "run 3 versus run 300" knob, and it's the direct answer to the scaling problem,
because it's no longer my attention that caps the fleet.

**Then it classifies, and this is the part I care most about.** Four outcomes, not
two. Done — gates green, PR open. Needs review — the agent finished but something
didn't verify, so it says so instead of claiming success. Blocked — the engine
*refused* to launch it, because a repo it depends on isn't clean. And error. At two
hundred repos, that triage is the product. A hundred green PRs I can't distinguish
from a hundred plausible-looking ones is worth nothing.

**One more piece that keeps the fleet coherent.** When a shared library lands clean,
the engine tags its PR head — `0.0.1.dev0` — and injects that exact pin
(`common-utils@0.0.1.dev0`) into the prompt for every consumer in a later wave,
then gates on whether the agent actually used it. That's the thing I told you I
do by hand today. It's now automatic, and it's what stops the fleet from drifting
mid-campaign.

**And what comes out.** A PR per repo, which a human merges — nothing auto-merges,
ever. A dashboard with the wave order and every outcome. And a usage record per
agent: the token breakdown, the raw API response, and a spend figure. That's the
budget answer — cost per execution, next to outcome per execution.

**[If pace allows]** Three things are deliberately pluggable: the gates, the
playbook, and the concurrency. Adding a verification rule doesn't touch the run
loop. I'll come back to that. *(Foreshadows the live extension.)*

**[TRANSITION → "So let's run it."]**

## 8 · Live demo [~9 min]

**[SHOW: editor, `targets/risk-scoring`]**

Let me show you one repo before anything touches it, because the demo only means
something if you believe the starting point.

This is representative of the fleet. A Makefile. A `requirements.txt` with loose
pins. `.python-version` says 3.11. And in the source — `datetime.utcnow()`, which is
gone in modern Python, and a pydantic v1 `class Config`, which needs the v2
migration.

**[Run `pytest` — green]**

Green today. That's the trap: nothing here is broken, so nothing here gets
prioritized. And the upgrade will break some of it — that's the point. Fixing what
an upgrade breaks is judgment work, and it's different in every repo. That's the
part you can't `sed`.

**[SHOW: terminal — `just demo`]**

So let's run the fleet.

**[First output: the dependency matrix]**

First thing it does — before it launches anything — is read the code. It walks every
checkout, pulls the distribution name out of `setup.py` or `pyproject.toml`, reads
the declared requirements, and parses the actual imports out of the AST. Then it
keeps only the edges that point at another repo in this fleet.

And it shows its work. `payments-ledger depends on risk-scoring — imports 'risk'`.
`notifications-svc depends on common-utils — declared requirement`. I'm not
hand-writing this graph in a config file and asking you to trust it. It's derived
from the repos, with the evidence printed next to each edge, so when the order looks
wrong you can see *why* it thinks that.

**[Next output: the wave plan]**

That graph topologically sorts into waves. Wave one, `common-utils` — the shared
library, nothing depends on it going first. Wave two, `notifications-svc` and
`risk-scoring` in parallel, because neither depends on the other. Wave three,
`payments-ledger`, which imports both.

This is the fleet-level piece a per-repo bot structurally cannot do. Dependabot has
no idea these four repos are related.

**[The run proceeds — narrate]**

Now it's launching. One Cloud Agent per repo, in its own isolated VM, each one
running that markdown playbook. Within a wave they run in parallel; between waves
there's a barrier — nothing in wave two starts until wave one is verified.

**[SHOW: pre-baked PR tab, while the run continues]**

While that works, let me show you what one of these actually produces — this is a
real PR from a live run yesterday.

The Makefile is gone, replaced by a `justfile`. `requirements.txt` is gone, replaced
by `pyproject.toml` and a `uv.lock`. The runtime moved to 3.14. And then the
interesting part — `utcnow()` became `now(UTC)`, and the pydantic v1 config became
v2.

That last bit is the whole argument. The first three changes are mechanical; I'd
script those. The last one is the agent doing judgment work that's different in
every repo. And it's all in one reviewable diff, with the test-green artifact
attached.

**[Back to the terminal — wave 1 completes]**

And there's the piece that ties the fleet together. `common-utils` came back clean,
so the engine tagged its PR head `0.0.1.dev0` — and that exact pin,
`common-utils@0.0.1.dev0`, gets injected into the prompt for every repo in wave
two that imports it. Then there's a gate checking whether the agent actually used it.

That's the thing I told you I do by hand today. Tag the library, tell the downstream
teams, chase them. It's now a step in the run.

**FALLBACK:** if a live agent stalls, say it plainly — "cloud agents take minutes and
cost credits, so I pre-ran the fleet; here's the real output" — and go to the
pre-baked PRs + recording. Composure over a spinner.

## 9 · Results — the dashboard [~3 min]

**[SHOW: `fleet_report.html`]**

Here's the whole campaign in one view. Wave order across the top, one card per repo.

Four outcomes, and the two in the middle are the ones I care about.

`common-utils` and `notifications-svc` — done. Gates green, PRs open, waiting for a
human. And notice `notifications-svc` passed `upstream_pins` — it actually consumed
the tag the library published one wave earlier.

`risk-scoring` — **needs review.** It couldn't resolve a transitive pin, an old
pydantic that won't move cleanly. And here's what matters: it *said so*. It didn't
weaken the test suite to get green, it didn't quietly skip the file. It finished,
failed its own gate, and flagged itself. Look at the gate row — it passed
`upstream_pins`, so this isn't a coordination failure; it pinned its upstream
correctly and got stuck on its own dependency. At two hundred repos, that precision
is the entire product. A hundred green PRs I can't distinguish from a hundred
plausible-looking ones is worth nothing.

`payments-ledger` — **blocked.** It imports `risk-scoring`, `risk-scoring` isn't
clean, so the engine refused to launch it at all. It didn't try and fail; it never
started. That's the difference between a coordinated campaign and a hundred
independent PRs racing each other.

**[Point at the cost receipt]**

And this run cost between a dollar thirteen and two fifteen — broken out by token
bucket, from the usage endpoint, per agent.

Two honest things about that number. The range exists because Cursor hasn't
published a cache-write rate for this model, so rather than pick a number and
present it as fact, it carries the uncertainty through and shows you the bound. And
the projection to the full fleet — forty-five to eighty-six dollars — assumes these
four repos are representative, which they aren't quite; one of them is deliberately
the gnarly one.

But the shape is the point. Spend per execution, next to outcome per execution.
That's the number the two-million-dollar budget can't produce today.

## 10 · Trade-offs and limits [~2 min]
**[Slide: trade-offs]**

Three decisions I'd defend, and three things that are genuinely not solved.

**Decisions.** Python against the REST API, not the richer TypeScript SDK — because
the customer lives in Python and I wanted the tooling native to their stack. If they
were a TS shop I'd flip that. Nothing auto-merges, ever; every result is a PR. And
blocking consumers on unclean dependencies — that's safety over throughput, and it
means a bad shared library stalls a branch of the fleet on purpose.

**Limits.** The gates are coarse — "tests green" is a real signal but it's not a
proof, and a determined agent can satisfy a weak gate. Polling, not webhooks, so
there's latency I don't need. And template reconciliation is the riskiest step in
the playbook: applying a shared template to a repo with twenty years of local
decisions in it is exactly where I'd expect this to produce something a human has to
unwind.

**And the honest one about cost:** spend scales linearly with the fleet. Which is why
triage matters more than throughput. The win isn't running two hundred agents fast —
it's that the twenty repos needing a human are *identified*, and the other hundred
and eighty are reviewable.

**Roadmap.** Webhook-driven instead of polling. Richer policy and supply-chain gates.
And the one I care about most: the same engine runs the *next* campaign. This one
happens to be "get to 3.14." The next is "rebuild every service on API v2," and
that's a different playbook file against the same orchestrator.

## 11 · Close + hand to Q&A [~1 min]

The through-line is one agent became a fleet, and then the fleet became an *ordered,
gated, triaged* campaign — where each step reused the same core, which is why it
extends cleanly rather than needing a rewrite.

And the shift I'd want you to take away: my attention stopped being the bottleneck,
and I got an audit trail and a cost-per-outcome number I've never had.

One direction I'd like to show you live, if there's appetite — encoding an org
compliance policy as a gate that applies across all two hundred repos at once. It's
about six lines. Happy to build that now, or take questions first.

---

## Timing cheat-sheet

| § | block | ~min | if you're behind, cut… |
|---|-------|------|------------------------|
| 1 | Background | 2.0 | trim to the "twenty years of drift" image |
| 2 | Business problem | 1.5 | keep — short, and it sets up §5 |
| 3 | Gap in existing tools | 2.0 | cut the Dependabot detail, keep **order** |
| 4 | How I run it today | 1.5 | merge into §5 |
| 5 | Why it falls short | 3.0 | keep all three — they're the payoff structure |
| 6 | The shift | 2.5 | cut the self-hosted-workers aside |
| 7 | Architecture | 4.0 | cut the "pluggable seams" beat |
| 8 | Live demo | 9.0 | show one PR well, not three |
| 9 | Dashboard | 3.0 | **never cut — the payoff** |
| 10 | Trade-offs | 2.0 | drop the roadmap, keep the limits |
| 11 | Close | 1.0 | keep the extension proposal |

Full length ≈ 31.5 min. Land at 28 by trimming §1/§4/§6 and you keep a buffer for
the demo running long.

**Protect §8's dependency-matrix beat and all of §9.** Everything else compresses.

## Payoff map (why the sections are in this order)

Each architecture and demo beat closes a loop opened earlier — keep this intact if
you re-cut the script:

| Promised in | Paid off by |
|---|---|
| §3 wholesale migration, not a bump | §7 playbook · §8 the PR diff |
| §3 order / topology | §7 scheduler · §8 dependency matrix + waves |
| §5 attention is the bottleneck | §7 concurrency knob · §8 parallel waves |
| §5 no audit record | §7 versioned playbook · §9 per-run usage record |
| §5 output per dollar | §9 cost receipt |
| §4 pushing dev tags by hand | §8 `0.0.1.dev0` auto-tag · §9 `upstream_pins` |

## Expected on-screen state (verify before you present)

`just demo` should end with:

```
common-utils         DONE           PASS:pr_opened PASS:deps_resolved PASS:tests_green
notifications-svc    DONE           PASS:… PASS:upstream_pins
risk-scoring         NEEDS REVIEW   FAIL:deps_resolved FAIL:tests_green PASS:upstream_pins
payments-ledger      BLOCKED        —
total=4  done=2  needs_review=1  blocked=1  error=0
```

If `notifications-svc` shows `FAIL:upstream_pins`, the mock isn't echoing the
injected `common-utils@0.0.1.dev0` pins — the fleet-coherence story will look
broken on stage, and §9's "it actually consumed the pin" line won't be true.
