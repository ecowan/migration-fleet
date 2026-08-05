# Architecture — migration-fleet

Low-level reference for answering questions from engineers. Covers the data
model, every module, how a **live** run executes end to end, the design
decisions worth defending, and the sharp edges someone reading the source will
find. Line references are to the current tree.

**Shape in one paragraph.** A Cursor Cloud Agent is one worker in an isolated
VM: it clones a repo, does the work, opens a PR. This project is the *foreman*
above that worker — ~1,000 lines of Python against the Cloud Agents v1 REST API
that (1) derives a dependency graph by reading the repos, (2) topologically
sorts it into waves, (3) fans out one agent per repo with bounded concurrency,
(4) runs verification gates on each result, (5) classifies into four outcomes,
(6) publishes a version tag when a shared library lands clean and injects that
pin into downstream prompts, and (7) writes a dashboard plus a per-agent token
receipt. Nothing auto-merges: every result is a PR.

---

## 1 · Inputs and outputs

**Inputs**

| | file | what it carries |
|---|---|---|
| Manifest | `repos.yaml` | repo list + url, `model`, `concurrency`, `block_on_upstream`, `pricing`, `fleet_size`, optional `routing`, `publish_tag` per repo |
| Playbook | `playbook/migration_playbook.md` | 68 lines of markdown — the migration itself, sent verbatim as the agent prompt |
| Checkouts | `targets/<name>/` | local clones, read only to build the dependency graph |

`depends_on` in YAML is **explicitly discarded** (`run.py:65`) — the matrix
derives it from repo contents and overwrites it.

**Outputs**

- One PR per repo (opened by the agent, `autoCreatePR: true`)
- `fleet_report.html` — self-contained dashboard (`fleet_report.dry-run.html` under `--dry-run`)
- `fleet_usage.json` — per-agent token buckets + the verbatim API body + priced receipt
- Console: dependency matrix, wave plan, per-repo step log, result table, spend receipt
- Git tag `0.0.1.dev0` on each `publish_tag` library's PR head

---

## 2 · The data model (`models.py`, 134 lines)

Everything else is a transformation over these. Framework-free dataclasses.

```
Status = PENDING | RUNNING | NEEDS_REVIEW | DONE | BLOCKED | ERROR
TERMINAL = {NEEDS_REVIEW, DONE, BLOCKED, ERROR}
```

**`RepoTarget`** — `name`, `url`, `ref="main"`, `depends_on` (filled by the
matrix, *not* YAML), `publish_tag`, `root`.
Semantics: every name in `depends_on` must migrate **before** this repo.

**`CheckResult`** — `name`, `passed`, `detail`. One gate's verdict.

**`AgentRun`** — the lifecycle record: `target`, `agent_id`, `status`, `pr_url`,
`summary`, `checks[]`, `error`, `model`, `duration_s`, `dev_tag`, `usage`,
`artifacts[]`. `gates_passed` = all checks passed.

Token accessors are defensive about response shape (`models.py:75-118`):
`RestCursorClient` stores the **whole** usage body (so unmodeled fields survive
to the log), while the mock returns a flat dict — `_usage_root()` accepts both.
`total_tokens` prefers the API's own `totalTokens` and falls back to summing
buckets, so a response omitting the total doesn't read as *free*.

**`WaveTiming`** / **`FleetResult`** — timing per wave and for the fleet.

---

## 3 · Module by module, in pipeline order

### `dep_matrix.py` (286) — derive the graph from repo contents

Requires a local checkout per repo at `roots/<name>/`; raises `FileNotFoundError`
naming every missing one (`:106`). For each repo, `_inspect_repo` collects:

- **dist name** — regex over `pyproject.toml` `name =`, else `setup.py`
- **import packages** — dirs with `__init__.py` under `src/` (or root), skipping `tests`/`test`/`docs`/dotdirs
- **declared deps** — `requirements*.txt`, `setup.py` `install_requires` block, `pyproject` dependency lists
- **imports** — `ast.walk` over `src/`, `tests/`, root; `ast.Import` and absolute `ast.ImportFrom` (`node.level == 0`), top-level module only

Then two indexes — `by_dist` (normalized dist name *and* repo name) and
`by_import` (top-level package) — and an edge is emitted whenever a repo's
declared requirement or import resolves to **another fleet member**. Externals
are dropped. `normalize_name` is PEP 503-ish (`[-_.]+ → -`, lowercase).

Every edge carries a **reason string** (`declared requirement 'common-utils'`,
`imports 'common'`) which is printed as evidence. `apply()` uses
`dataclasses.replace` so `root`/`publish_tag` survive.

### `scheduler.py` (81) — topological sort into waves

Kahn's algorithm grouped by level. Only in-fleet dependencies count toward
indegree (`:50`). `ready` is sorted for determinism.

**Cycle handling** (`:65-72`): if nothing has indegree 0 but repos remain,
everything left is grouped into one wave, annotated
`"dependency cycle — human must choose a break point"`, and returned with
`cycle_nodes` populated. It flags rather than hangs.

### `router.py` (251, optional) — per-repo model selection

Off unless `--route-models` or `routing.enabled`. Scores each repo from LOC,
breaking pins (regexes for pydantic 1.x, django 1/2.x, flask 1.x), number of
in-fleet dependents, upstream count, and `publish_tag`, then maps the score to
`easy`/`mid`/`hard` model ids via `easy_max=3` / `mid_max=7`. Deterministic, no
API calls, cached per repo, and every score carries `reasons` for `--verbose`.

### `cursor_client.py` (450) — the API boundary

`CursorClient` ABC with six methods; two implementations. Auth is HTTP Basic,
API key as username, empty password. Base `https://api.cursor.com/v1`.

The v1 model is a durable **agent** plus per-prompt **runs**: creating an agent
enqueues an initial run, and status/PR/result live on the *run*. The client
keeps `_runs: agent_id → run_id` in memory.

| method | call |
|---|---|
| `launch` | `POST /agents` |
| `poll` | `GET /agents/{id}/runs/{runId}` |
| `usage` | `GET /agents/{id}/usage` |
| `followup` | `POST /agents/{id}/runs` |
| `cancel` | `POST /agents/{id}/runs/{runId}/cancel` |
| `list_artifacts` | `GET /agents/{id}/artifacts` |

**Launch body** (`:99`): client-supplied `agentId = bc-<uuid4>`,
`prompt.text`, `repos: [{url, startingRef}]`, `autoCreatePR: true`, `name`,
optional `model.id`, optional `env: {type: cloud, name}`.

The client-supplied id makes create **idempotent across transport retries** — a
replayed POST returns 409 and `_adopt_existing` fetches the agent instead of
creating a duplicate (`:116`). `cancel` treats 409 (`run_not_cancellable`) as
success.

`poll` normalizes to `{done, ok, status, pr_url, branch, summary, duration_ms,
raw}`, walking `git.branches[]` for the first `prUrl`.
`_FINISHED = {FINISHED, COMPLETED, SUCCEEDED}`, `_FAILED = {ERROR, FAILED,
CANCELLED, EXPIRED}`.

`usage` deliberately returns the **whole body**, not just `totalUsage` — a cost
field or rate multiplier Cursor adds later would otherwise be dropped and
unrecoverable after the process exits (`:208`).

`MockCursorClient` finishes after 2 polls, can force one repo to NEEDS_REVIEW
(`flaky_repo`), can inject fake 429s to exercise retry paths, and parses fleet
pins back out of the prompt so the `upstream_pins` gate passes in dry-run.

### `retries.py` (100) — transient failure policy

`RETRYABLE_STATUS = {429, 502, 503, 504}`. `with_retries` (default 6 attempts)
retries those plus `TimeoutException`/`TransportError`, preferring the
`Retry-After` header and otherwise using exponential backoff (base 2, cap 60s)
with jitter. `is_retryable_error` does substring matching on a stored error
string so the orchestrator can re-queue a whole repo after the client has
already exhausted its own attempts.

### `gates.py` (85) — the trust layer

A gate is `Callable[[poll_dict], CheckResult]`. That's the entire contract,
which is why adding one is a function plus a list entry.

- `pr_opened_gate` — truthiness of `poll["pr_url"]`
- `deps_resolved_gate` — substring scan of the summary
- `tests_green_gate` — substring scan of the summary
- `make_upstream_pins_gate(required_pins)` — closure; searches summary + `str(raw)` for `name@version` or `name==version`, reports the missing ones

`DEFAULT_GATES` is the first three; `upstream_pins` is appended **per repo at
runtime** when that repo has upstream tags. See §6 for why the two substring
gates are the weakest thing in the codebase.

### `tags.py` (238) — fleet version coordination

`FLEET_DEV_VERSION = "0.0.1.dev0"`, a module constant. `DevTag` is frozen with
derived forms: `pin` (`name@0.0.1.dev0`), `pep508` (`name==0.0.1.dev0`),
`uv_source` (a `[tool.uv.sources]` entry pointing at the git tag — no package
index required).

`render_pins_prompt()` builds the markdown block appended to the playbook for
consumers: pin by **version**, resolve via `[tool.uv.sources]`, don't keep
sibling path hacks, and list every pin under an "Upstream pins" heading in the
PR description using `name@version` — which is exactly what the gate then looks
for. **The prompt and the gate are two halves of one contract.**

`GitHubTagPublisher.publish()` resolves the PR head sha
(`GET /repos/{o}/{r}/pulls/{n}` → `head.sha`), then `POST git/refs`. On 422 the
tag exists: if it's already at that sha it's reused, otherwise it's **moved**
with `PATCH ... force: true` (`:228`).

### `orchestrator.py` (420) — the run loop

`FleetOrchestrator` holds the client, base prompt, gates, an
`asyncio.Semaphore(concurrency)`, poll settings, `max_polls=300`,
`wave_retries=2`, the tag publisher, the optional router, and three callbacks
(`on_progress`, `on_wave`, `on_step`). `_fleet_tags: name → DevTag` accumulates
in-process as libraries land.

**`run_one(target)`** — the per-repo state machine:

1. `_prompt_for()` — base playbook + `render_pins_prompt()` for any `depends_on` already in `_fleet_tags`; returns the required-pins dict
2. Copy the gate list (`gates = list(self._gates)` — so the per-repo append can't leak) and add `upstream_pins` if pins are required
3. Router assess/route, or the fleet default model
4. Acquire the semaphore
5. `launch` → `Status.RUNNING`
6. Poll loop up to `max_polls`, sleeping `poll_interval` between checks
7. Timeout → `ERROR` + `_cancel_quiet` + `_enrich`; agent-reported failure → `ERROR`
8. Otherwise record `pr_url`/`summary`, run every gate, and classify: **all passed → DONE, else NEEDS_REVIEW**
9. `_enrich` — usage and artifacts, each in its own try (best-effort; missing telemetry never fails a run)
10. `_maybe_publish_tag` — only for `DONE` repos with `publish_tag`; a tag failure flips the run to `ERROR`

The whole body is wrapped in `except Exception` (`:201`) so one repo's failure
is recorded against that repo and can't take down the fleet.

**`run_scheduled(schedule, block_on_upstream)`** — wave by wave:

- For each repo, compute blockers: any in-fleet dependency whose result is not `DONE`, **or** a `publish_tag` upstream that is `DONE` but has no tag (`:305`). Blocked repos get `Status.BLOCKED` and are **never launched**.
- `await asyncio.gather(*(run_one(r) for r in runnable))` — a barrier at the wave boundary
- Then up to `wave_retries` passes re-running repos that died on *retryable* errors, **before** `block_on_upstream` freezes their consumers (`:355`) — so a 429 doesn't cascade into a blocked subtree
- Record `WaveTiming`; return runs in `schedule.order`

### `pricing.py` (251) — bucket-level cost

Published composer-2.5 rates per MTok: standard `$0.50 in / $2.50 out / $0.20
cache read`; fast `$3.00 / $15.00 / $0.50` (fast is the product default).

**Cache *write* is unpublished.** Rather than invent a number, the receipt
prices it as an interval — `CACHE_WRITE_BOUNDS = (0.0, 1.25)` × the input rate —
so every total is a low/high range until you set `cache_write_per_mtok` in
`repos.yaml`. Overrides that disagree with the tier get relabelled `custom (...)`
so the receipt can't silently misattribute rates.

### `usage_log.py` (131) — the audit sidecar

`SCHEMA_VERSION = 2`. Writes per-repo token buckets, status, duration, model,
PR url, and `usage_raw` (the API body verbatim), plus fleet totals, bucket
shares, and — only if rates are configured — a priced receipt. Pricing is
optional **by design**: the measurement can be re-rated later without re-running
the fleet.

### `report.py` (593) · `live_status.py` (178) · `fleet_map.py` (585)

Presentation. `report.py` renders the self-contained HTML dashboard and the
console table, collapsing agent markdown into short blurbs. `live_status.py` is
a sticky bottom-of-TTY progress bar with an elapsed ticker. `fleet_map.py` is a
read-only overview (`just map`) — repo table, edges with evidence, waves,
digraphs, mermaid — using local checkouts for the graph and GitHub for live
tags/PRs.

### `run.py` (417) — composition root

Argparse (`--dry-run` / `--live` mutually exclusive, **required**), output path
resolution, config/playbook load, matrix build, optional router, client and tag
publisher selection, orchestrator construction, schedule, run, then render.
All wiring, no logic.

---

## 4 · How a live run works, end to end

`just run --live --verbose` →
`uv run python run.py --live --verbose`

1. **Resolve output paths.** `--live` → `fleet_report.html` / `fleet_usage.json`. (`--dry-run` gets a `.dry-run` infix so a simulated run can never overwrite a real dashboard.)
2. **Load** `repos.yaml` and the playbook. Build `RepoTarget`s, discarding any YAML `depends_on`.
3. **Scan checkouts.** `build_dep_matrix()` reads every `targets/<name>/`, extracts dist names, packages, declared requirements, and AST imports, keeps only in-fleet edges, and prints the matrix with evidence per edge. `matrix.apply()` rewrites `depends_on`.
4. **Route** (if enabled) — score each repo, print the routing table.
5. **Build clients.** `RestCursorClient(CURSOR_API_KEY, environment=…)` with `poll_interval=5.0s`, `wave_retry_delay=10.0s`. GitHub token from `GITHUB_TOKEN`/`GH_TOKEN`/`gh auth token`; without one it warns that **fleet tags will not be published**, which silently disables the coordination story.
6. **Schedule.** `build_schedule()` → waves; print them, and warn on cycles.
7. **Run wave by wave:**
   - Compute blocked repos (dirty upstream, or an untagged `publish_tag` upstream). They never launch.
   - For each runnable repo, in parallel up to `concurrency`:
     `POST /agents` with the playbook (plus any upstream-pin block) → poll `GET /agents/{id}/runs/{runId}` every 5s → on terminal status run the gates → classify DONE/NEEDS_REVIEW → fetch usage + artifacts → if it's a clean library, resolve the PR head sha and create/move `refs/tags/0.0.1.dev0`, recording the pin in `_fleet_tags`.
   - Re-queue retryable failures up to twice before blocking consumers.
   - Barrier; next wave. Repos in wave N+1 that depend on a tagged library now get `common-utils@0.0.1.dev0` injected into their prompt, plus an `upstream_pins` gate checking they used it.
8. **Report.** Console table, `fleet_report.html`, `fleet_usage.json`, and the spend receipt (a range while cache-write is unpriced).

**What a human does next:** review and merge the PRs. Nothing auto-merges, ever.

---

## 5 · Design decisions worth defending

| Decision | Why | What it cost |
|---|---|---|
| **Python + REST, not the TS SDK** | Customer lives in Python; wanted tooling native to their stack | Gave up the richer SDK (streaming, subagents). Would flip for a TS shop. |
| **Graph derived from repo contents, not config** | A hand-authored graph is wrong the moment someone adds an import; evidence per edge makes it auditable | Needs a local checkout of every repo — the first thing that breaks at fleet scale |
| **Waves, not a free-for-all** | Migrating a consumer against an un-migrated dependency produces spurious failures you can't distinguish from real ones | A wave waits on its slowest repo |
| **Four outcomes, not two** | At 120 repos, triage *is* the product; a hundred green PRs you can't distinguish from a hundred plausible ones is worth nothing | More classification surface to get right |
| **Nothing auto-merges** | Regulated customer: human review is non-negotiable | Throughput is bounded by review bandwidth, not agents |
| **Block consumers on dirty upstreams** | Safety over throughput, deliberately | One bad shared library stalls a branch of the fleet |
| **Playbook as a markdown file in git** | Versioned, reviewable, diffable — half the audit answer. Two repos differing → `git blame` the instructions | Prompt changes need a commit, not a config toggle |
| **Client-supplied `agentId`** | Makes create idempotent; a retried POST returns 409 instead of double-charging you for a duplicate agent | Slightly more code on the 409 path |
| **Store the raw usage body** | Anything Cursor adds later (a cost field, a rate multiplier) survives to the log instead of being dropped | Bigger sidecar |
| **Price cache-write as an interval** | The rate is genuinely unpublished; a point estimate would be fabricated precision | Every total is a range, which needs explaining |
| **Pricing separate from measurement** | Re-rate an old run without re-running the fleet | Two steps instead of one |
| **Broad `except Exception` per repo** | One repo's failure must not kill a 120-repo campaign | Can mask a programming error as an agent failure |

---

## 6 · Sharp edges — what a careful reader will find

Be ready to name these before they're pointed out. Volunteering them is
stronger than defending them.

### The substring gates are the weakest code in the project

`tests_green_gate` and `deps_resolved_gate` do naive substring matching on the
agent's own prose. Verified behavior:

| agent summary | `tests_green` | correct? |
|---|---|---|
| `"tests are not green"` | **PASS** | no |
| `"I could not get the tests green"` | **PASS** | no |
| `"no unresolved dependencies remain"` | `deps_resolved` **FAIL** | no |

Both directions are wrong. The module docstring is honest that a live version
"would shell out / call GitHub checks against the agent's PR" and that these
read the poll payload so the whole thing runs in dry-run — but as written,
**the gate trusts the agent's self-report and parses it badly.**

*The answer:* the gate *abstraction* is right — one function, one
`CheckResult`, swappable without touching the run loop. The gate
*implementations* are demo-grade. Real ones read the PR's CI status via the
GitHub checks API or parse a junit artifact. That's a substitution, not a
redesign. `pr_opened` and `upstream_pins` are structural and don't have this
problem.

### The fleet tag is mutable

`FLEET_DEV_VERSION` is one constant for every package, and re-running a library
**force-moves** the tag to the new PR head (`tags.py:228`). A consumer that
pinned `0.0.1.dev0` yesterday resolves to different code today. Fine inside a
single campaign; a real supply-chain problem if those pins escape one. Wants a
content-addressed or monotonically-incrementing version.

### No persistence, no resume

The entire run is an in-memory `results: dict` in one asyncio process. No
checkpoint. A 5-minute demo never notices; a multi-hour 120-repo run that dies
in wave 6 of 9 loses its state and re-spends the credits.

### The wave barrier is coarser than the graph

`asyncio.gather` per wave means a repo waits for its whole wave, not just its
own upstreams. The graph already knows better — releasing each repo as soon as
*its* dependencies are clean is a strictly better executor and the information
is already there.

### Single tenancy throughout

`DEFAULT_GATES` is one global list; `repos.yaml` holds one model, one
concurrency, one policy; every agent runs as one `CURSOR_API_KEY` with no
notion of *which user* requested the run; `fleet_report.html` and the
`0.0.1.dev0` namespace are global. The identity gap is the real blocker for a
second team — it can't authorize or attribute.

### Smaller things

- `_parse_pyproject_deps` is a self-described "minimal TOML-free scrape" by regex, but `requires-python = ">=3.11"` means **`tomllib` is in the stdlib**. There's no good reason not to parse it properly.
- `_read_imports` has a comment about dropping stdlib noise (`dep_matrix.py:279`) directly above `return mods` — the filtering was never implemented. Harmless, since only fleet packages match `by_import`, but the comment lies.
- `poll` takes the **first** branch with a `prUrl`; an agent that opened more than one PR would be under-reported.
- The mock fabricates PR numbers as `40 + seq` against real repo URLs, so dry-run dashboards link to PRs that don't exist. (Why dry-run output now has its own filename.)
- `FleetOrchestrator.run()` — the unscheduled fan-out — exists but nothing calls it; `run.py` always uses `run_scheduled`.

---

## 7 · Fast answers to likely questions

**"How is this different from Dependabot?"** Those are dependency tools: they
keep a version current, per repo, in steady state. This re-tools the repo
(template, build system, runtime) and coordinates it across the fleet with
dependency-ordered scheduling and triage. The version bump is one step in a
playbook of twelve.

**"How do you trust the output?"** I don't — I gate it and a human merges. And
the gates are currently coarse; see §6. The structural ones (`pr_opened`,
`upstream_pins`) are sound; the test/dep ones want the GitHub checks API.

**"What happens when it gets it wrong?"** Three containment layers: per-repo
isolation (one failure can't touch the fleet), the NEEDS_REVIEW class (honest
partial results instead of false green), and human review before merge. Nothing
is irreversible — everything is a PR.

**"Why poll instead of webhooks?"** Simplicity for a first version. It costs
latency (5s cadence) and API calls. Webhooks are the roadmap item.

**"What's the concurrency limit really bounded by?"** Locally, one semaphore.
In practice: API rate limits, credits, and review bandwidth — which is why
triage matters more than raw throughput.

**"Does our code leave our infrastructure?"** The platform supports self-hosted
worker pools, so agents can run on your own infra.

**"How long to make this production-ready for N teams?"** Durable state and
per-team config are well-understood work. The identity and authorization model
is the piece I'd want to design with your security people before writing code.
