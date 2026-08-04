# Migration Fleet — Cursor Cloud Agent orchestrator
# List recipes: `just`

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Create/update .venv from the lockfile
sync:
    uv sync

# Deterministic simulation (no API key, no credits)
dry-run *args:
    uv run python run.py --dry-run {{args}}

# Live Cloud Agent fleet (needs CURSOR_API_KEY)
live *args:
    uv run python run.py --live {{args}}

# Presentation narration over a dry-run
demo:
    uv run python run.py --dry-run --verbose

# Open the HTML dashboard
report:
    open fleet_report.html
