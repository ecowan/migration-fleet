# 30-Minute Demo — Script

Format: **30-min demo** (this doc) → 15-min Q&A → 15-min live extension (you propose it).
Audience: all engineers, may not share your migration context.

**No slides, and nothing pre-baked.** Everything on screen is either you talking or
something happening live. No recording, no PR from yesterday's run, no dry-run
dashboard standing in for a real one. Every artifact you show — the PRs, the
tags, the dashboard, the cost receipt — is produced by the run the room watches
you start.

That's the strongest version of this demo, and it's also the one with no net. Two
consequences you have to design around, and the script below does:

1. **You cannot show a PR until the run opens one.** The PR walkthrough moves to
   *after* wave one lands. Everything before it must stand on its own.
2. **Your fallback is composure and candor, not a backup tab.** See the FALLBACK
   block at the end of §8. Rehearse it out loud like any other beat.

Pre-flight (before you share screen):
- Editor open on the project, with these already open as tabs so you're not
  fumbling in a file tree: `repos.yaml`, `playbook/migration_playbook.md`,
  `orchestrator/` expanded, `targets/risk-scoring/`.
- **Two terminal panes.** Left is where `just run --live --verbose` runs. Right is
  free — that's where `just map` goes while the fleet polls.
- **Browser: the GitHub PR list for the fleet org**, filtered to open PRs, and
  *empty* or clearly stale when you start. You'll refresh it live. Do not have a
  PR detail page pre-loaded — refreshing an empty list into a real PR in front of
  the room is the whole point.
- `fleet_report.html` **closed.** This run overwrites it. If you leave a tab open
  from a rehearsal you will show stale numbers; open it fresh in §9. (A dry run
  can no longer clobber it — `--dry-run` writes `fleet_report.dry-run.html`
  instead — so anything at `fleet_report.html` is real. It may still be *old*.)
- Mic + screen-share tested. Editor and terminal fonts bumped for a projector.

**This is a live run** — `just run --live --verbose`, real Cloud Agents, real
credits, ~5 min. Before the session:
- `CURSOR_API_KEY` exported *in the shell you'll present from*, not just your dev
  shell.
- `GITHUB_TOKEN` or `gh auth` working — `just map` and the PR refresh both need it.
- Enough credits for a full fleet run **plus a retry**, in case you have to
  restart in the room.
- Rehearse live end-to-end at least twice and confirm the outcome mix is
  **2 DONE · 1 NEEDS_REVIEW · 1 BLOCKED**. That mix is *not* guaranteed on a live
  run — read "Expected on-screen state" at the bottom before you rehearse. With
  nothing pre-baked to fall back on, this is now the single biggest risk in the
  demo.

---

## 1 · Background [~1.5 min]

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
template so every repo has the same shape. Now do that across a hundred and twenty
repositories.

## 2 · Business problem [~1 min]

Here's the thing about this work: it's always at the bottom of the task list.
None of it is ever "breaking today," so it never wins against a feature or a
customer bug. So it waits — until a critical vulnerability gets published, or two
libraries finally refuse to work together, and suddenly it's an emergency. And
when that happens, it yanks engineers off the work that actually moves the
business — features, real bugs — to go do a migration nobody scheduled.

## 3 · The gap in existing tools [~1.25 min]

There are tools in this space. Dependabot and friends will open a PR bumping a
version number, and that's genuinely useful. Their scope ends at the version
number, though. Restructuring the project, adding your build tooling, applying
your template — all of that is still yours to do. That's the gap.

And there's a second thing they don't touch: order. In a shop like this, repos
depend on each other — lots of internal libraries. You can't just fire a hundred
and twenty migrations at once. You have to understand the topology — who imports what — and
migrate from the lowest layers up. Fix the shared library first, get it green,
then the things that depend on it, and so on. Get the order wrong and you're
migrating against a moving target.

## 4 · This is real — how I run it today [~0.75 min]

This isn't hypothetical for me. I've been doing exactly this since March. With
today's AI tools it's already faster than by hand — but it could be far faster,
because of how I'm running it. Right now I'm babysitting: monitoring windows,
managing processes on my own machine, approving each git push, reviewing and
pushing the dev tags by hand. I'm in the loop for every step.

## 5 · Why that falls short — three ways [~2.25 min]

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

**And the bar any fix has to clear.** Because this is a regulated shop, four
constraints are non-negotiable, and they shaped every decision you're about to see:

- **Auditable.** Every change traceable to the instructions that produced it.
- **Human-reviewed.** Nothing reaches a main branch without a person approving it.
- **No business-logic drift.** A migration modernizes the scaffolding. If it
  quietly changes what the service *does*, it has failed, even if tests are green.
- **Isolated.** One repo's migration cannot touch another's.

Hold onto those four. Every one of them pushes toward the same answer: a fleet of
gated PRs with a human at the end of each one.

## 6 · The shift — a cloud agent system [~2.5 min]

So here's the change I want to make. Instead of me at my machine driving one
migration at a time, you build a cloud agent system once, and run it — periodically,
on demand, across the whole fleet — and you get concrete numbers back: spend per
execution, and outcome per execution.

And this is where running on a cloud platform specifically changes the game —
versus the local-first way I've been working, and versus tools like Claude Code
that run on your own machine:

**Unattended and parallel.** The agents run in the cloud, so I launch a fleet and
walk away. The babysitting disappears entirely, and my attention stops being the
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

Contrast that with local-first. A local agent is powerful, and the moment you have a
fleet, the fleet-level tooling becomes your problem — provisioning machines,
wrangling git worktrees, aggregating logs, capping concurrency yourself. The cloud
platform hands you that whole layer, which leaves the migration logic as the only
thing you actually have to write.

**[TRANSITION → "Let me show you what that looks like."]**

## 7 · Architecture — the worker and the foreman [~6.5 min]

> **No diagram — drive this from the editor.** Every beat below has a file behind
> it. Open the file, then say the words. This is the section that most needs a
> visual, and the code is a better one than a box diagram: it's the actual thing.

**[SHOW: editor, project root]**

Start with the primitive, because everything else sits on top of it.

A Cursor Cloud Agent is one worker in an isolated VM. It clones a repo, does the
work, opens a pull request. That's the unit — the **worker**. What I built sits a
layer above it: the **foreman**. It decides what runs, in what order, and whether
to trust what comes back. Roughly a thousand lines of Python against the Cloud
Agents REST API.

**Two inputs — and I'd rather show you than describe them.**

**[OPEN: `repos.yaml`]**

Input one, the manifest. That's the whole fleet: the repo list, and — right here —
`concurrency: 5`. One number. That's the "run 3 versus run 300" knob, and I'll
come back to it.

**[OPEN: `playbook/migration_playbook.md`]**

Input two, the playbook — the migration itself, written as markdown. Adopt the
copier template, convert Make to just, move to uv, get to 3.14, fix what the
upgrade breaks. And this is the point I want to land: **this is a file in git.**
It lives where your code lives — versioned, reviewed in a PR, diffable. That's the
first half of the auditing answer I said I didn't have twenty minutes ago. When two
repos come out differently, I can `git blame` the instructions and point at exactly
which revision each one got.

**[OPEN: `orchestrator/` in the file tree — leave it expanded]**

And the foreman is essentially five files. I'll take them in the order the fleet
hits them, because that order *is* the pipeline — this is the diagram, it's just
made of code.

**`dep_matrix.py` → `scheduler.py` — these order the fleet.** This is the piece I said Dependabot structurally
can't do. It topologically sorts the dependency graph into *waves* — repos in a wave
have no dependency on each other and run in parallel; waves run in sequence. The
shared library migrates and goes green before anything that imports it. And because
import cycles are a real thing in a twenty-year-old codebase — service A and B
importing each other — it detects them and flags them for a human instead of hanging.

**`orchestrator.py` — runs each wave.** Bounded fan-out, one agent per repo. It
launches, polls until the agent reaches a terminal state, then runs verification
gates against the result. Concurrency is that single number you just saw in the
manifest — the direct answer to the scaling problem, because it's no longer my
attention that caps the fleet.

**`gates.py` — and this is the smallest file that matters most.**

**[OPEN: `orchestrator/gates.py`]**

A gate is just a function: it takes the agent's result and returns pass or fail
with a reason. That's the whole contract. `pr_opened`. `deps_resolved`.
`tests_green`. `upstream_pins`. Adding a fifth is adding a function to this list —
nothing in the run loop changes. Remember that shape; I'll come back to it at the
end.

**Then it classifies, and this is the part I care most about.** Four outcomes.
Done — gates green, PR open. Needs review — the agent finished, something failed to
verify, and it says so. Blocked — the engine *refused* to launch it, because a repo
it depends on is still dirty. And error. At a hundred and twenty repos, that triage
is the product. A hundred green PRs I can't distinguish from a hundred
plausible-looking ones is worth nothing.

Most systems here give you two buckets: worked, or didn't. The middle two are where
all the value is.

**`tags.py` — one more piece that keeps the fleet coherent.** When a shared library lands clean,
the engine tags its PR head — `0.0.1.dev0` — and injects that exact pin
(`common-utils@0.0.1.dev0`) into the prompt for every consumer in a later wave,
then gates on whether the agent actually used it. That's the thing I told you I
do by hand today. It's now automatic, and it's what stops the fleet from drifting
mid-campaign.

**And what comes out.** A PR per repo, which a human merges — nothing auto-merges,
ever. A dashboard with the wave order and every outcome. And a usage record per
agent: the token breakdown, the raw API response, and a spend figure. That's the
budget answer — cost per execution, next to outcome per execution.

**So — three seams, and you've now seen all three on screen.** The manifest's
concurrency number. The playbook markdown. The gate functions. Behavior lives in
data and small functions, which is what keeps the run loop stable while the policy
around it changes. That's where I'd go if we have time at the end.
*(Foreshadows the live extension.)*

**[TRANSITION → "So let's run it — for real."]**

## 8 · Live demo [~9 min]

**[SHOW: editor, `targets/risk-scoring`]**

Let me show you one repo before anything touches it, because the demo only means
something if you believe the starting point.

This is representative of the fleet. A Makefile. A `requirements.txt` hard-pinned
to `pydantic==1.10.2` and a `common-utils==1.2.0` that nobody has moved in years.
`.python-version` says 3.11. A `setup.py`.

**[OPEN: `src/risk/score.py` — and follow the import]**

And here's the source. Notice the very first thing it does:
`from common.money import Money, current_hour`. It imports the shared library.

**[OPEN: `targets/common-utils/src/common/money.py`]**

So let's go look at what it's importing. `datetime.utcnow()` — deprecated on 3.12
and headed for removal. A pydantic v1 `class Config` — that's the v2 migration.

This is the whole argument for ordering, in two files. The deprecated code isn't
*in* `risk-scoring`; it reaches it through a dependency. Migrate `risk-scoring`
first and you're migrating it against a library that's about to change underneath
it. That's why `common-utils` goes in wave one.

> **Accuracy check — this beat used to be wrong.** `utcnow()` and `class Config`
> live in `common-utils/src/common/money.py`, **not** in `risk-scoring/src/`. Open
> the right file. (There's a second `class Config` in
> `payments-ledger/src/ledger/ledger.py` if you want a third data point.)

**[Run `just test-one risk-scoring` — green]**

> Use the recipe, not a bare `pytest`. `just test` used to call `python -m pytest`,
> and there is no bare `python` on this machine — it dies with
> `command not found`. Both recipes now use `uv run`. Verify before you present.

Green today. That's the trap: nothing here is broken, so nothing here gets
prioritized.

**[If the deprecation warnings show — use them, don't skip past]**

And look at that — pytest is already telling us. `PydanticDeprecatedSince20:
support for class-based config is deprecated`. The warning has been in the output
for two years. It's green, so nobody reads it.

The upgrade will turn some of those warnings into failures — that's the point.
Fixing what an upgrade breaks is judgment work, and it's different in every repo.
That's the part you can't `sed`.

**[SHOW: left terminal pane — `just run --live --verbose`]**

So let's run the fleet. This is live — real Cloud Agents, real repos, real credits.

> **Pacing.** The run takes ~5 min and the beats below carry ~7 min of material —
> deliberately over-supplied, with `just map` as the designated cut.
>
> **The `T+` stamps are estimates. Replace them with real ones after your first
> rehearsal.** They assume wave one lands around T+4:00, which is a guess — the
> only stamp that actually matters is when wave one's PR appears, because the
> whole back half of §8 hangs off it. Time that once and write it in.
>
> The beats are **event-anchored, not clock-anchored**: everything before the PR
> beat works with no PR on screen, and the PR beat fires whenever wave one lands,
> early or late. Never let the narration run dry before the run does — that's the
> only failure mode here, and `just map` is the lever against it.

**[T+0:00 — first output: the dependency matrix]**

First thing it does — before it launches anything — is read the code. It walks every
checkout, pulls the distribution name out of `setup.py` or `pyproject.toml`, reads
the declared requirements, and parses the actual imports out of the AST. Then it
keeps only the edges that point at another repo in this fleet.

And it shows its work. `payments-ledger depends on risk-scoring — imports 'risk'`.
`notifications-svc depends on common-utils — declared requirement`. Every edge is
derived from the repos themselves, with the evidence printed right next to it — so
when the order looks wrong, you can see *why* it thinks that and go check. Nobody
has to take a hand-written config file on faith.

**[T+1:00 — next output: the wave plan]**

That graph topologically sorts into waves. Wave one, `common-utils` — the shared
library, nothing depends on it going first. Wave two, `notifications-svc` and
`risk-scoring` in parallel, because neither depends on the other. Wave three,
`payments-ledger`, which imports both.

This is the fleet-level piece a per-repo bot structurally cannot do. Dependabot has
no idea these four repos are related.

**[T+1:45 — the run proceeds — narrate the step log]**

Now it's launching. One Cloud Agent per repo, in its own isolated VM, each one
running that markdown playbook. Within a wave they run in parallel; between waves
there's a barrier — nothing in wave two starts until wave one is verified.

And because I'm running verbose, you can watch the state machine: `launch`, then
`poll` — that's it waiting on a real VM doing real work — then `gates`, then
`classify`, then `tag`. Every one of those lines is an audit record.

**[T+2:15 — back to the editor, `targets/risk-scoring` — no PR exists yet]**

> **Why this beat is here.** Nothing is pre-baked, so there is no PR to show until
> the run opens one. This block and the next exist to hold the room honestly for
> the ~2 min before that happens. Neither needs anything on screen that doesn't
> already exist.

While those work, let me answer the question I'd be asking if I were you: *why not
just write a codemod?*

And honestly — for a lot of this, you should. Make to just, the pin bumps, the file
moves: that's deterministic, and a script is cheaper and more predictable than an
agent. I'm not arguing otherwise, and I'd script it.

**[Back to `common-utils/src/common/money.py` — the `class Config` block]**

But this is the thing I showed you two minutes ago. Pydantic v1 `class Config`
becomes a v2 `model_config` — and *how* depends on which validators this repo
happened to use, which fields are optional, what the surrounding code does with
them. `payments-ledger` has its own `class Config` in `ledger.py`, with different
fields around it, and it needs a different answer. Multiply that by a hundred and
twenty repos where every team made slightly different choices twenty years apart.

You can't regex your way through judgment. That's the part the agent earns — and
it's exactly why the gates exist. The agent does the variable work; the gates keep
it honest. In about ninety seconds you'll see both halves of that in one diff.

**[T+3:00 — ELASTIC, THE DESIGNATED CUT: right terminal pane — `just map`]**

> Run `just map` with the token live — mid-run it fills the `TAG / LATEST PR`
> column as the fleet actually opens PRs, which is the point of doing this live.
> `--offline` exists if the network is hostile, but you lose the column that makes
> this beat worth having. **Rehearse the invocation you'll actually use.**

This is a read-only view of the same graph, and it's how I actually look at the
fleet day to day.

Four things in one screen. The repo table, with what each one imports and what
imports it. The edges, with the evidence — `payments-ledger → risk-scoring`,
*declared requirement and imports 'risk'*. The merge waves. And the one I find
myself looking at most: the **fleet pins** graph —
`common-utils@0.0.1.dev0 ──pin──► notifications-svc, risk-scoring,
payments-ledger`. That's the coordination problem drawn as a picture: one library
tag, three consumers that have to take it.

So mid-campaign I can answer "where is the fleet right now" without reading a log:
what's landed, what's in flight, what's waiting on an upstream that hasn't gone
green. At four repos that's a nicety. At a hundred and twenty it's the difference
between running a campaign and hoping.

> **This is your slack.** Stretch it by walking the edges table row by row and
> then the pins digraph; re-run it to show the PR column filling in. Drop it
> entirely if wave one has already landed. Nothing later depends on it.

**[T+4:00 — left pane — wave 1 completes]**

There's the piece that ties the fleet together. `common-utils` came back clean, so
the engine tagged its PR head `0.0.1.dev0` — and that exact pin,
`common-utils@0.0.1.dev0`, gets injected into the prompt for every repo in wave two
that imports it. Then there's a gate checking whether the agent actually used it.

That's the thing I told you I do by hand today. Tag the library, tell the downstream
teams, chase them. It's now a step in the run.

**[T+4:15 — browser: refresh the open-PR list. THE MOMENT. Don't rush it.]**

And now I can show you what one of these actually produces — because it exists now
and it didn't when I started talking.

> Refresh the list first, *then* click through. Let the room watch the row appear.
> Say the next line while the diff loads, not before.

This PR was opened by an agent about ninety seconds ago, in a VM I never touched.

**[Walk the diff]**

The Makefile is gone, replaced by a `justfile`. `requirements.txt` is gone,
replaced by `pyproject.toml` and a `uv.lock`. The runtime moved to 3.14. And then
the part I just told you to watch for — `utcnow()` became `now(UTC)`, and the
pydantic v1 config became v2.

The first three are the codemod half. The last one is the judgment half. Both in
one reviewable diff, with the test-green artifact attached, waiting for a human.

Nobody approved a push. Nobody watched a window. That's the whole shift.

---

**FALLBACK — read this twice, because there is no backup tab.**

Nothing is pre-baked, which is a deliberate choice and also means a stall has no
escape hatch. Your fallback is candor plus useful airtime, in this order:

1. **Name it immediately and without apology.** "These are real VMs doing real
   work, and sometimes one takes a bad five minutes. Let me keep going while it
   works." Do not narrate a spinner. Do not refresh repeatedly in silence.
2. **Spend the time on `just map`** — it's live, it's read-only, and it works
   regardless of what the agents are doing.
3. **Pull Q&A forward.** "While that finishes — what would you want this pointed
   at in your stack?" You have 15 min of Q&A budgeted after this; borrowing five
   of it early costs you nothing and it beats dead air. This is the strongest
   save available and you should be genuinely willing to use it.
4. **If the run is truly dead:** stop it, say so plainly, and go to §9 using the
   `fleet_report.html` from your last rehearsal — stating out loud that it's from
   a rehearsal run this morning, not the one they just watched. Honesty about
   provenance costs you far less than a discovered pre-baked artifact would.
5. **Do not restart the fleet mid-demo.** Five more minutes of silence is worse
   than any of the above.

The audience forgives a slow agent. They don't forgive watching you panic at one,
and they *really* don't forgive finding out the "live" run was a recording.

## 9 · Results — the dashboard [~3 min]

**[SHOW: open `fleet_report.html` — freshly written by the run that just finished]**

> Open it *now*, don't switch to a tab you had open. The room should see the file
> the run just wrote. If you have a stale tab from rehearsal, you'll show last
> night's numbers and the whole live-only premise dies on the spot.
>
> The PR links in here are clickable and **real** — they come straight from the
> agents' API responses. Clicking one is a good optional beat. (Dry-run reports
> fabricate PR numbers that 404, which is why they now write to a separate
> `fleet_report.dry-run.html`.)

Here's the whole campaign in one view — this file did not exist four minutes ago.
Wave order across the top, one card per repo.

Four outcomes, and the two in the middle are the ones I care about.

`common-utils` and `notifications-svc` — done. Gates green, PRs open, waiting for a
human. And notice `notifications-svc` passed `upstream_pins` — it actually consumed
the tag the library published one wave earlier.

> **Read the actual failure reason off the screen — don't recite this one.** The
> line below describes what *usually* fails. Live, the gate detail may say
> something else entirely, and reading out a reason that contradicts what's on
> screen is the worst thing that can happen in this section. The beat works with
> any reason; only the *shape* matters — it failed, and it said so. Glance at the
> gate row, then say it in your own words.

`risk-scoring` — **needs review.** It couldn't resolve a transitive pin, an old
pydantic that won't move cleanly. And here's what matters: it *said so*. It left
the test suite intact, left the file alone, finished, failed its own gate, and
flagged itself for a human. Look at the gate row — it passed `upstream_pins`, so
the coordination worked exactly as designed; it pinned its upstream correctly and
then got stuck on its own dependency. At a hundred and twenty repos, that precision
is the entire product. A hundred green PRs I can't distinguish from a hundred
plausible-looking ones is worth nothing.

`payments-ledger` — **blocked.** It imports `risk-scoring`, `risk-scoring` is
dirty, so the engine refused to launch it at all. It never started — the wave
barrier held. That's the difference between a coordinated campaign and a hundred
independent PRs racing each other.

**[Point at the cost receipt]**

> **Same rule — read the real number.** `$1.13–$2.15` was a previous run. A live
> run will land somewhere else, and the projection scales off it. Say whatever the
> receipt says; the argument doesn't depend on the figure. If it came in higher
> than rehearsal, that's *worth saying out loud* — "more than my last run, because
> one of these agents worked harder" is a better answer than a suspiciously stable
> number, and it's the kind of variance a real budget owner needs to hear about.

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

## 10 · Trade-offs and limits [~2.5 min]

> No slide here, and this is the one section where that genuinely costs you — six
> bullets delivered as pure speech is hard to follow. Fix it by **counting out
> loud**: "three decisions I'd defend — one, two, three. Three things that aren't
> solved — one, two, three." The verbal scaffolding replaces the slide.

Three decisions I'd defend, and three things that are genuinely not solved.

**Decisions.** Python against the REST API. The TypeScript SDK is richer, and I
gave that up deliberately — the customer lives in Python and I wanted the tooling
native to their stack. If they were a TS shop I'd flip that. Second: every result
is a PR, and nothing auto-merges, ever. Third: blocking consumers on dirty
dependencies — that's safety over throughput, and it means a bad shared library
stalls a branch of the fleet on purpose.

**Limits.** The gates are coarse. "Tests green" is a real signal, and it stops well
short of a proof — a determined agent can satisfy a weak gate. Second: I poll,
which costs me latency I could get back with webhooks. Third: template
reconciliation is the riskiest step in the playbook — applying a shared template to
a repo with twenty years of local decisions in it is exactly where I'd expect this
to produce something a human has to unwind.

**And the honest one about cost:** spend scales linearly with the fleet, which is
why triage matters more than throughput. The win is that the twenty repos needing a
human are *identified*, and the other hundred and eighty are reviewable. Raw speed
across a hundred and twenty agents would buy me much less.

**Roadmap.** Webhook-driven instead of polling. Richer policy and supply-chain gates.
And the one I care about most: the same engine runs the *next* campaign. This one
happens to be "get to 3.14." The next is "rebuild every service on API v2," and
that's a different playbook file against the same orchestrator.

## 11 · Close + hand to Q&A [~1 min]

The through-line is one agent became a fleet, and then the fleet became an *ordered,
gated, triaged* campaign — each step reusing the same core, which is what lets the
next one bolt on cleanly.

And the shift I'd want you to take away: my attention stopped being the bottleneck,
and I got an audit trail and a cost-per-outcome number I've never had.

One direction I'd like to show you live, if there's appetite — encoding an org
compliance policy as a gate that applies across all hundred and twenty repos at
once. It's about six lines. Happy to build that now, or take questions first.

---

## Timing cheat-sheet (re-priced for no slides)

The old budgets assumed slide pauses — §1–6 was priced at ~73 words/min, which
only works when the room is reading something. Talking straight to camera you'll
run 130–140. These numbers are re-derived from actual word counts at **135 wpm**,
plus real screen-work time where you're opening files or switching panes.

| § | block | words | ~min | if you're behind, cut… |
|---|-------|-------|------|------------------------|
| 1 | Background | 153 | 1.5 | trim to the "twenty years of drift" image |
| 2 | Business problem | 100 | 1.0 | keep — short, and it sets up §5 |
| 3 | Gap in existing tools | 152 | 1.25 | cut the Dependabot detail, keep **order** |
| 4 | How I run it today | 92 | 0.75 | merge into §5 |
| 5 | Why it falls short | 253 | 2.25 | the four constraints can go to two |
| 6 | The shift | 306 | 2.5 | cut the self-hosted-workers aside |
| 7 | Architecture (editor-driven) | 770 | 6.5 | skip opening `gates.py`, just describe it |
| 8 | Live demo | 1167 | 9.0 | **drop the `just map` beat** — it's designed to go |
| 9 | Dashboard | 335 | 3.0 | **never cut — the payoff** |
| 10 | Trade-offs | 272 | 2.5 | drop the roadmap, keep the limits |
| 11 | Close | 118 | 1.0 | keep the extension proposal |

**Full length ≈ 31.25 min.** Land at 29 by trimming §4 into §5 and dropping the
self-hosted aside in §6 — that keeps a buffer for the live run going long, which
it will at some point. §8 is the only section whose length you don't fully
control; everything else absorbs the variance.

**Protect:** §7's three file-opens (they replace the diagram), §8's
dependency-matrix beat, and all of §9.

### Where the no-slides time went

Removing slides didn't just delete visuals — it deleted ~5 min of pause time from
the front half. That time was reallocated deliberately, not padded:

- **§5, +0.75** — the four enterprise constraints (audit, human review, no
  business-logic drift, isolation). Was runbook-only; it's the clearest FDE signal
  in the deck and it sets up why §7 is gates-and-PRs.
- **§7, +2.5** — the pipeline diagram became four file-opens. `repos.yaml`,
  `migration_playbook.md`, the `orchestrator/` tree, `gates.py`. Better than the
  diagram was: it's the actual artifact, and opening `gates.py` foreshadows §11's
  extension.
- **§8, +2.5 of *material*** (not runtime) — the codemods rebuttal and the
  `just map` view, both sized to cover the 5-min live run. See below.
- **§10, +0.5** — counting the six items out loud, which is slower than reading
  them off a slide.

### Covering the 5-minute live run

The run is ~5 min. §8's during-run block carries **773 words ≈ 5.7 min of speech**,
and with the map pane, the PR refresh and the diff walk it plays at **~7 min** —
deliberately over-supplied, because with nothing pre-baked you cannot afford to
run dry.

**The order changed for live-only.** The PR walkthrough used to sit at T+2:15
against a pre-baked tab. It can't — no PR exists yet — so it moved behind wave one
and the two filler beats moved forward to hold the gap. This is a better narrative
anyway: the PR you show is one the room watched get created.

| beat | at | ~min | elastic? |
|---|---|---|---|
| dependency matrix | T+0:00 | 1.0 | no — protect it |
| wave plan | T+1:00 | 0.75 | no |
| launch + verbose step log | T+1:45 | 0.5 | slightly |
| "why not a codemod" (editor, no PR needed) | T+2:15 | 1.0 | slightly |
| `just map` | T+3:00 | 1.0–2.5 | **yes — the designated cut / the slack** |
| wave 1 lands + `0.0.1.dev0` tag | T+4:00 | 0.5 | no |
| **live PR refresh + diff walk** | T+4:15 | 1.5 | yes — stretch on the pydantic hunk |

**All `T+` values are estimates — replace them after your first live rehearsal.**
The only one that matters is when wave one's PR actually appears; the last two
rows hang off it. If wave one is slow, `just map` absorbs it. If wave one is fast,
cut `just map` and go straight to the PR.

The failure mode is finishing your narration before the run finishes. Never fill
with "so… it's still running" — pull Q&A forward instead (see the FALLBACK block).

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
| §5 four enterprise constraints | §7 `gates.py` + versioned playbook · §9 triage |
| §7 `gates.py` is one function | §11 the extension proposal ("about six lines") |
| §8 "you can't regex judgment" | §9 `risk-scoring` needs-review, honestly flagged |

## Expected on-screen state (verify before you present)

The target mix, which §9 is written against:

```
common-utils         DONE           PASS:pr_opened PASS:deps_resolved PASS:tests_green
notifications-svc    DONE           PASS:… PASS:upstream_pins
risk-scoring         NEEDS REVIEW   FAIL:deps_resolved FAIL:tests_green PASS:upstream_pins
payments-ledger      BLOCKED        —
total=4  done=2  needs_review=1  blocked=1  error=0
```

### ⚠️ This mix is NOT guaranteed on a live run

Read this before you rehearse. `repos.yaml:16` sets `flaky_repo: risk-scoring`,
and that setting is **dry-run only** — it's consumed by `MockCursorClient`
(`orchestrator/cursor_client.py:295`), and `run.py:349` only prints the
`demo inject` line when `--dry-run` is set. On `--live` there is no injection.

So the `1 NEEDS_REVIEW` outcome that §9 leans on is not forced live — it happens
only if the real agent genuinely fails `deps_resolved`/`tests_green` on
`risk-scoring`. And `payments-ledger` is BLOCKED *because* `risk-scoring` isn't
clean, so if `risk-scoring` comes back DONE you lose **two** of §9's three hero
moments in one go — the honest-failure beat and the blocking beat — and the
dashboard shows a much less interesting `4 DONE`.

Going live-only makes this the **single biggest risk in the demo**, because the
obvious mitigation — keep the dry-run dashboard in a tab — is exactly the kind of
pre-baked artifact you've ruled out. So it has to be fixed upstream, before the
session.

**Do this — it's no longer optional:**

**Make `risk-scoring` genuinely un-migratable.** Put a real, unresolvable
transitive pin in its requirements so a competent agent *cannot* get it green and
correctly reports that. Then the failure is a property of the repo, reproducible
on every run, and no injection is involved.

This is the honest version, it's what §9's script already claims is happening
("it couldn't resolve a transitive pin"), and it's the only approach that makes
the outcome mix deterministic under a live run. Once it's real, verify it across
**at least two** full live rehearsals — "reliably" is the operative word, and one
green run doesn't prove it.

**If you get on stage and it comes back `4 DONE` anyway**, don't fake it and don't
apologize. Pivot out loud:

> "Interesting — the fleet went four-for-four today, which is the boring outcome.
> The interesting behavior is what happens when an agent *can't* finish, so let me
> show you the gate that would have caught it and what the triage looks like."

Then open `gates.py` and walk the `deps_resolved` gate and the four-outcome
classifier as code rather than as a result. You lose the dashboard's drama; you
keep the entire argument. Rehearse this pivot once so it isn't the first time
you've said it.

### Other live-run checks

- `notifications-svc` showing `FAIL:upstream_pins` means the injected
  `common-utils@0.0.1.dev0` pin wasn't consumed — the fleet-coherence story looks
  broken on stage and §9's "it actually consumed the pin" line won't be true.
- `CURSOR_API_KEY` exported in the presenting shell, not just your dev shell.
- `GITHUB_TOKEN` / `gh auth` working — both `just map`'s PR column and §8's live
  PR refresh depend on it.
- Enough credits for a full fleet run plus at least one retry.
- The `.cursor/` environment files on `main` in all four seeded repos, or agents
  fail at launch rather than doing work.

### Reset between rehearsals (live-only makes this matter)

You'll rehearse this live more than once, and a dirty starting state is the most
likely way to look bad. Before each run:

- **`just close-prs`** — closes every open PR, deletes the head branches, and
  deletes the `0.0.1.dev0` fleet tags across all four repos. Both matter: stale
  PRs break §8's "refresh the empty list" moment, and an existing tag makes the
  wave-one tagging beat a silent no-op with nothing to point at.
- **`just clean`** so §9's "this file did not exist four minutes ago" is literally
  true. (It removes both the live and the `.dry-run` artifacts.)
- **Revert the target repos to their pre-migration state** on `main`.

The last step is still manual — worth folding into a `just reset-demo` alongside
the other two rather than doing it by hand under time pressure. Forgetting one of
these mid-rehearsal is cheap; forgetting one on stage is not.
