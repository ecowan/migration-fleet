# Migration Fleet — Cursor Cloud Agent orchestrator

A **fleet modernization engine**: run coordinated, opinionated migration
campaigns across a whole repo fleet with Cursor Cloud Agents. One agent per repo,
each running a versioned migration playbook in a custom environment, ordered by a
cross-repo dependency graph, each verified and opened as a PR for human review —
orchestrated from Python against the Cloud Agents REST API.

## What this is (and what it isn't)

This is **not** a dependency bot. Dependabot / Renovate — even with Copilot
autofix — keep *versions* current, continuously, one repo at a time. This engine:

- **Re-tools the repo**, not just its versions: imposes a project template,
  swaps the build system (Make → just), the dep tooling (pip → uv), and the
  runtime (3.11 → 3.14).
- **Encodes one enterprise's standard** as a reusable, versioned playbook run
  consistently across 100+ repos — the same engine runs the *next* campaign
  (e.g. "rebuild every service on API v2").
- **Coordinates across repos**: a dependency-sorted scheduler migrates shared
  libraries before their consumers, and holds a consumer back if its dependency
  isn't clean. No dependency bot has a fleet view or migration ordering.

> Problem (sanitized): a large regulated financial org runs 100+ Python
> services with inconsistent tooling (Makefile builds, ad-hoc structure) pinned
> to Python 3.11. They need to standardize onto a copier template with `uv` +
> `just` and upgrade to Python 3.14 with dependency resolution. By hand this is
> months of repetitive, error-prone toil; one interactive agent session per repo
> doesn't scale to 100+. This fans it out.

## Architecture

```
repos.yaml ─┐
            │   ┌─────────────────────── FleetOrchestrator ───────────────────────┐
playbook ───┼──▶│  bounded-concurrency fan-out                                     │
            │   │    for each repo:                                                │
CURSOR_API ─┘   │      CursorClient.launch()  ──▶  Cloud Agent (custom env)        │
                │      poll until terminal    ◀──   (uv · just · copier · py3.14)   │
                │      run verification gates  ──▶  PR + artifacts                  │
                │      classify: DONE / NEEDS_REVIEW / ERROR                        │
                │   └──────────────────────────────────────────────────────────────┘
                └──▶ report.py ──▶ HTML dashboard + console table
```

Key modules (`orchestrator/`):

| file | responsibility |
|------|----------------|
| `dep_matrix.py`    | scans each checkout (`requirements.txt` / `setup.py` / imports) to build in-fleet `depends_on` |
| `scheduler.py`     | dependency-sorts the fleet into migration **waves** (shared libs first); detects cycles |
| `cursor_client.py` | REST client (`RestCursorClient`) + deterministic `MockCursorClient` behind one interface |
| `orchestrator.py`  | wave-by-wave execution, bounded concurrency within a wave, polling, gates, upstream-blocking |
| `gates.py`         | verification gates (PR opened, deps resolved, tests green) — the "trust" layer |
| `report.py`        | self-contained HTML dashboard (with the wave order) + console table |
| `models.py`        | small framework-free dataclasses |

### Dependency-ordered scheduling

At startup, `dep_matrix` reads each local checkout under `targets/` and builds
`depends_on` from declared requirements and cross-repo imports. That graph is
topologically sorted into waves. Repos in a wave run in parallel; waves run in
sequence, so `common-utils` migrates and verifies **before** the services that
import it. If a dependency lands in `NEEDS_REVIEW`, its consumers are held
`BLOCKED` rather than migrated against an unclean base. Import cycles are
detected and flagged for a human instead of hanging the run.

## Run it

The orchestrator runs on the same tooling it migrates repos *toward* — `uv` + `just`:

```bash
just install          # uv sync
just demo             # dry run: no API key, no credits
open fleet_report.html
```

Live run:

```bash
export CURSOR_API_KEY=key_...              # Cursor API key (Basic auth)
just run --live                            # or: uv run python run.py --live
```

`just --list` shows every recipe (`install`, `demo`, `run`, `test`, `lock`, `clean`).
No `uv`? A `requirements.txt` is still provided: `pip install -r requirements.txt && python run.py --dry-run`.

For a live run, first **seed the demo repos on GitHub** (push the folders under
`targets/` to real repos your key can access) and point `repos.yaml` `url`s at
them. Each target must include a Cursor environment so agents get the playbook
toolchain (Python 3.14, uv, just, copier):

```
.cursor/Dockerfile          # copy from environment/Dockerfile
.cursor/environment.json    # copy from environment/environment.json
```

Cursor resolves `.cursor/environment.json` in the **cloned target repo** (not
this orchestrator). Optional: `--environment <name>` still works for a named
dashboard env, but Path A (per-repo `.cursor/`) is the default.

## Design seams (built to extend live)

- **Add a verification gate** — append a function to `gates.py` (e.g. a policy
  gate: "no dependency with a known CVE"). The run loop doesn't change.
- **Swap Mock ↔ REST** — one constructor swap; nothing else moves.
- **Scale 3 → 300** — the `concurrency` knob in `repos.yaml`.
- **Change the playbook** — it's just markdown passed as the agent prompt.

## Guardrails / enterprise posture

- Nothing auto-merges; every result is a PR for human review.
- Agents run in isolated VMs with per-environment scoped secrets.
- Repos that can't be fully resolved land in `NEEDS_REVIEW`, not silently "done".
- Failures are isolated per repo — one bad repo never sinks the fleet.
