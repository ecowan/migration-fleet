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

# close open PRs + delete 0.0.1.dev0 fleet tags on every repo in repos.yaml
# (also deletes PR head branches); needs `gh auth login` / GH_TOKEN
close-prs:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run python - <<'PY'
    import json
    import subprocess
    import sys
    from pathlib import Path

    import yaml

    from orchestrator.tags import FLEET_DEV_VERSION, parse_github_repo

    cfg = yaml.safe_load(Path("repos.yaml").read_text())
    closed = 0
    tags_deleted = 0
    for r in cfg["repos"]:
        url, name = r["url"], r["name"]
        print(f"== {name} ({url}) ==")
        try:
            out = subprocess.check_output(
                [
                    "gh", "pr", "list",
                    "--repo", url,
                    "--state", "open",
                    "--json", "number,title",
                ],
                text=True,
            )
        except FileNotFoundError:
            print("  error: `gh` not found on PATH", file=sys.stderr)
            sys.exit(1)
        except subprocess.CalledProcessError as exc:
            print(f"  error: listing PRs failed ({exc.returncode})", file=sys.stderr)
            sys.exit(exc.returncode)
        prs = json.loads(out)
        if not prs:
            print("  (no open PRs)")
        for pr in prs:
            num, title = pr["number"], pr["title"]
            print(f"  closing #{num}: {title}")
            subprocess.check_call(
                [
                    "gh", "pr", "close", str(num),
                    "--repo", url,
                    "--delete-branch",
                ],
            )
            closed += 1

        owner, repo = parse_github_repo(url)
        tag = FLEET_DEV_VERSION
        exists = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/git/ref/tags/{tag}"],
            capture_output=True,
            text=True,
        )
        if exists.returncode != 0:
            print(f"  (no tag {tag})")
        else:
            subprocess.check_call(
                [
                    "gh", "api", "-X", "DELETE",
                    f"/repos/{owner}/{repo}/git/refs/tags/{tag}",
                ],
            )
            print(f"  deleted tag {tag}")
            tags_deleted += 1
    print(f"\nClosed {closed} PR(s); deleted {tags_deleted} tag(s).")
    PY

# run the target-repo "before" test suites
# uses `uv run` like every other recipe — a bare `python` is not on PATH on macOS
test:
    #!/usr/bin/env bash
    set -euo pipefail
    for r in common-utils risk-scoring notifications-svc payments-ledger; do
        echo "== $r =="
        ( cd targets/$r && uv run python -m pytest -q )
    done

# run ONE target repo's tests — this is the §8 demo beat
# usage: just test-one risk-scoring
test-one REPO="risk-scoring":
    cd targets/{{REPO}} && uv run python -m pytest -q

# regenerate the dependency lockfile
lock:
    uv lock

# remove caches and generated artifacts (including timestamped run outputs)
clean:
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    rm -rf outputs
    rm -f fleet_report.html fleet_usage.json
    rm -f fleet_report.dry-run.html fleet_usage.dry-run.json
