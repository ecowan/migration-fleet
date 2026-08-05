# migration-fleet — orchestrate Cursor Cloud Agents across a repo fleet.
# The orchestrator itself runs on the same tooling it migrates repos toward: uv + just.

# list recipes
default:
    @just --list

# install dependencies into the uv-managed environment
install:
    uv sync

# run the fleet in dry-run mode (no API key, no credits)
demo:
    uv run python run.py --dry-run --verbose

# run live (needs CURSOR_API_KEY + seeded GitHub repos); pass extra args through
run *ARGS:
    uv run python run.py {{ARGS}}

# dependency map + merge waves + latest GitHub tags/PRs (tables + digraphs)
# uses GITHUB_TOKEN or `gh auth token`; pass --offline to skip remote
map *ARGS:
    uv run python -m orchestrator.fleet_map {{ARGS}}

# run the target-repo "before" test suites
test:
    #!/usr/bin/env bash
    set -euo pipefail
    for r in common-utils risk-scoring notifications-svc payments-ledger; do
        echo "== $r =="
        ( cd targets/$r && python -m pytest -q )
    done

# regenerate the dependency lockfile
lock:
    uv lock

# remove caches and generated artifacts
clean:
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    rm -f fleet_report.html
