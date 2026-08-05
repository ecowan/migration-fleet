#!/usr/bin/env python3
"""Entry point for the migration fleet orchestrator.

Dry run (no API key, no credits — deterministic simulation):
    just dry-run

Live run (hits api.cursor.com; needs CURSOR_API_KEY and seeded GitHub repos):
    export CURSOR_API_KEY=key_...
    just live -- --environment my-migration-env

Presentation narration (wave banners, gate detail, PR links):
    just dry-run -- --verbose
    # or: just demo

Writes an HTML dashboard (fleet_report.html; fleet_report.dry-run.html under
--dry-run, so a simulated run can't overwrite a real one) and a console table.
While a run is in progress, a sticky bottom status bar shows a progress bar and
a live elapsed-time counter.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml

from orchestrator import (
    FleetOrchestrator,
    MockCursorClient,
    RepoTarget,
    RestCursorClient,
    Status,
    console_table,
    format_duration,
    render_html,
    write_usage_log,
)
from orchestrator.pricing import Rates, format_receipt
from orchestrator.dep_matrix import build_dep_matrix, format_matrix
from orchestrator.live_status import LiveStatusBar
from orchestrator.router import ModelTiers, router_from_matrix
from orchestrator.scheduler import Wave, build_schedule
from orchestrator.tags import GitHubTagPublisher, MockTagPublisher

HERE = Path(__file__).parent


def load_config(path: Path) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def load_playbook(path: Path) -> str:
    return path.read_text()


def _targets_from_config(cfg: dict, *, targets_root: Path) -> list[RepoTarget]:
    """Build RepoTargets; YAML depends_on is ignored (matrix overwrites)."""
    targets = []
    for raw in cfg["repos"]:
        row = dict(raw)
        row.pop("depends_on", None)
        row.pop("root", None)  # checkout root comes from --targets-root
        targets.append(RepoTarget(**row, root=targets_root))
    return targets


def _github_token() -> str:
    """Prefer GITHUB_TOKEN; fall back to `gh auth token`."""
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if tok:
        return tok.strip()
    try:
        import subprocess
        out = subprocess.check_output(
            ["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL
        )
        return out.strip()
    except Exception:  # noqa: BLE001
        return ""


def _compact_progress(run, bar: LiveStatusBar | None = None) -> None:
    dur = f"  {format_duration(run.duration_s)}" if run.duration_s is not None else ""
    line = f"  [{run.status.value:<12}] {run.target.name}{dur}"
    if bar and bar.active:
        bar.update(run)
        bar.log(line)
    else:
        print(line)


def _verbose_wave(
    wave: Wave, runnable: list, blocked: list, bar: LiveStatusBar | None = None
) -> None:
    names = ", ".join(r.name for r in wave.repos)
    lines = [f"\n── Wave {wave.index + 1} · {names} ──"]
    if wave.note:
        lines.append(f"   note: {wave.note}")
    if runnable:
        parallel = " in parallel" if len(runnable) > 1 else ""
        lines.append(
            f"   launching {len(runnable)} agent{'s' if len(runnable) != 1 else ''}{parallel}"
        )
    if blocked:
        lines.append(
            f"   holding {len(blocked)} repo{'s' if len(blocked) != 1 else ''} "
            f"(upstream not clean)"
        )
    if bar and bar.active:
        bar.set_wave(wave.index, [r.name for r in wave.repos])
        for line in lines:
            bar.log(line)
    else:
        print("\n".join(lines))


def _verbose_progress(run, bar: LiveStatusBar | None = None) -> None:
    name = run.target.name
    lines: list[str] = []

    if run.status is Status.RUNNING:
        lines.append(f"\n  ▶ {name}")
        lines.append(f"    repository  {run.target.url}")
        if run.agent_id:
            lines.append(f"    agent       {run.agent_id}")
        lines.append("    status      Cloud Agent working — waiting for PR + artifacts")
    elif run.status is Status.BLOCKED:
        lines.append(f"\n  ■ Holding {name}")
        lines.append(f"    reason      {run.error}")
        lines.append(
            "    decision    refuse to migrate a consumer against an unclean dependency"
        )
    elif run.status is Status.ERROR:
        lines.append(f"\n  ✗ {name} failed")
        lines.append(f"    error       {run.error}")
    elif run.status is Status.DONE:
        lines.append(f"\n  ✓ {name} — migration complete, ready for human review")
    elif run.status is Status.NEEDS_REVIEW:
        lines.append(f"\n  ⚠ {name} — needs human review (not silently marked done)")
    else:
        _compact_progress(run, bar)
        return

    if run.status in (Status.DONE, Status.NEEDS_REVIEW, Status.ERROR):
        if run.summary and run.status is not Status.ERROR:
            lines.append(f"    summary     {run.summary}")
        if run.duration_s is not None:
            lines.append(f"    duration    {format_duration(run.duration_s)}")
        if run.total_tokens:
            lines.append(f"    tokens      {run.total_tokens:,}")
        if run.artifacts:
            lines.append("    artifacts")
            for a in run.artifacts[:5]:
                lines.append(f"      · {a.get('path') or '?'}")
        if run.dev_tag:
            lines.append(f"    fleet pin   {run.dev_tag}")
        if run.checks:
            lines.append("    gates")
            for c in run.checks:
                mark = "PASS" if c.passed else "FAIL"
                detail = f" — {c.detail}" if c.detail else ""
                lines.append(f"      {mark}  {c.name}{detail}")
        if run.status is not Status.ERROR:
            if run.pr_url:
                lines.append(f"    pull request {run.pr_url}")
            else:
                lines.append("    pull request (none returned)")

    if bar and bar.active:
        bar.update(run)
        for line in lines:
            bar.log(line)
    else:
        print("\n".join(lines))


async def main() -> int:
    ap = argparse.ArgumentParser(description="Cursor Cloud Agent migration fleet")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="simulate, no API calls")
    mode.add_argument("--live", action="store_true", help="real Cloud Agent runs")
    ap.add_argument("--config", type=Path, default=HERE / "repos.yaml")
    ap.add_argument("--playbook", type=Path, default=HERE / "playbook/migration_playbook.md")
    ap.add_argument(
        "--targets-root",
        type=Path,
        default=HERE / "targets",
        help="local checkouts used to build the dependency matrix",
    )
    ap.add_argument("--environment", default=None, help="Cursor custom environment id (live)")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="dashboard path (default: fleet_report.html, "
             "or fleet_report.dry-run.html under --dry-run)",
    )
    ap.add_argument(
        "--usage-out",
        type=Path,
        default=None,
        help="per-bucket token usage sidecar (raw API response preserved); "
             "default: fleet_usage.json, or fleet_usage.dry-run.json under --dry-run",
    )
    ap.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="presentation-quality narration (waves, gates, PRs)",
    )
    ap.add_argument(
        "--route-models",
        action="store_true",
        help="pick model per repo from LOC/complexity (see routing: in repos.yaml)",
    )
    args = ap.parse_args()

    # A dry run writes to its own filenames. The mock fabricates PR urls
    # (`<repo>/pull/40+n`) that 404 against the real repos, so a simulated
    # dashboard must never overwrite the one a live run produced. An explicit
    # --out/--usage-out still wins.
    suffix = ".dry-run" if args.dry_run else ""
    if args.out is None:
        args.out = HERE / f"fleet_report{suffix}.html"
    if args.usage_out is None:
        args.usage_out = HERE / f"fleet_usage{suffix}.json"

    cfg = load_config(args.config)
    playbook = load_playbook(args.playbook)
    targets = _targets_from_config(cfg, targets_root=args.targets_root)

    # Read each checkout and derive who-imports-whom before scheduling.
    print(f"\nScanning checkouts under {args.targets_root}/ for in-fleet dependencies…")
    matrix = build_dep_matrix(targets, roots=args.targets_root)
    targets = matrix.apply(targets)
    print(format_matrix(matrix))

    route_cfg = cfg.get("routing") or {}
    use_router = args.route_models or bool(route_cfg.get("enabled"))
    router = None
    if use_router:
        default_model = cfg.get("model") or "composer-2.5"
        tiers = ModelTiers(
            easy=route_cfg.get("easy") or default_model,
            mid=route_cfg.get("mid") or default_model,
            hard=route_cfg.get("hard") or default_model,
            easy_max=int(route_cfg.get("easy_max", 3)),
            mid_max=int(route_cfg.get("mid_max", 7)),
        )
        router = router_from_matrix(
            matrix,
            targets,
            roots=args.targets_root,
            default_model=default_model,
            tiers=tiers,
        )
        print("\nModel routing (LOC + complexity):")
        for line in router.summary_lines():
            print(line)

    bar = LiveStatusBar([t.name for t in targets])

    def _log_line(line: str) -> None:
        if bar.active:
            bar.log(line)
        else:
            print(line)

    def on_api_retry(message: str) -> None:
        """Client-level backoff notices (429/5xx) — always useful, even without -v."""
        _log_line(f"    ↻ {message}")

    gh_token = ""
    if args.dry_run:
        client = MockCursorClient(flaky_repo=cfg.get("flaky_repo"))
        # Keep a short poll cadence in a TTY so the live status bar / timer can tick.
        poll_interval = 0.35 if bar.active else 0.0
        wave_retry_delay = 0.2
    else:
        api_key = os.environ.get("CURSOR_API_KEY", "")
        client = RestCursorClient(
            api_key,
            environment=args.environment,
            on_retry=on_api_retry,
        )
        poll_interval = 5.0
        wave_retry_delay = 10.0
        gh_token = _github_token()
        if not gh_token:
            print("warning: no GitHub token — fleet version tags will not be published")

    def on_progress(run) -> None:
        if args.verbose:
            _verbose_progress(run, bar)
        else:
            _compact_progress(run, bar)

    def on_wave(wave, runnable, blocked) -> None:
        if args.verbose:
            _verbose_wave(wave, runnable, blocked, bar)
        elif bar.active:
            bar.set_wave(wave.index, [r.name for r in wave.repos])

    def on_step(repo: str, message: str) -> None:
        """Fine-grained step log used only in --verbose."""
        prefix = "fleet" if repo == "*" else repo
        _log_line(f"    · [{prefix}] {message}")

    def on_tag_step(message: str) -> None:
        """Publisher-internal steps (resolve PR head, create ref, …)."""
        _log_line(f"    · [tag] {message}")

    if args.dry_run:
        tag_publisher = MockTagPublisher(
            on_step=on_tag_step if args.verbose else None
        )
    elif gh_token:
        tag_publisher = GitHubTagPublisher(
            gh_token,
            on_step=on_tag_step if args.verbose else None,
        )
    else:
        tag_publisher = None

    orch = FleetOrchestrator(
        client,
        prompt=playbook,
        model=cfg.get("model"),
        concurrency=cfg.get("concurrency", 3),
        poll_interval=poll_interval,
        wave_retries=2,
        wave_retry_delay=wave_retry_delay,
        tag_publisher=tag_publisher,
        router=router,
        on_progress=on_progress,
        on_wave=on_wave if args.verbose or bar.active else None,
        on_step=on_step if args.verbose else None,
    )

    # Dependency-sort the fleet into migration waves (shared libs first).
    schedule = build_schedule(targets)
    print("\nMigration order (dependency-sorted):")
    for w in schedule.waves:
        tag = f"  wave {w.index + 1}: " + ", ".join(r.name for r in w.repos)
        print(tag + (f"   [{w.note}]" if w.note else ""))
    if schedule.cycle_nodes:
        print(f"  !! cycle detected among: {', '.join(schedule.cycle_nodes)}")

    mode_label = "dry-run" if args.dry_run else "LIVE"
    print(f"\nLaunching {len(targets)} migration agents "
          f"({mode_label}, concurrency={cfg.get('concurrency', 5)}/wave)")
    if args.verbose:
        if router:
            print("  model        per-repo via router (see table above)")
        else:
            print(f"  model        {cfg.get('model') or '(default)'}")
        print(f"  poll every   {poll_interval:g}s  (max 300 polls/repo)")
        print(f"  API retries  6/call · wave re-queue ×2 (delay {wave_retry_delay:g}s)")
        print(f"  fleet tags   0.0.1.dev0 publish "
              f"{'on' if tag_publisher else 'OFF'} "
              f"(libs with publish_tag: true)")
        print(f"  block upstream consumers when a dependency isn't clean: "
              f"{cfg.get('block_on_upstream', True)}")
        if args.dry_run and cfg.get("flaky_repo"):
            print(f"  demo inject  {cfg['flaky_repo']} → NEEDS_REVIEW (dry-run only)")
        print("  step log     on (launch → poll → gates → classify → tag)")

    await bar.start_ticker()
    try:
        result = await orch.run_scheduled(
            schedule, block_on_upstream=cfg.get("block_on_upstream", True)
        )
    finally:
        await bar.stop()

    if args.verbose:
        print("\n── Fleet result ──")
        for w in result.waves:
            names = ", ".join(w.repo_names)
            print(f"  wave {w.index + 1} ({names}): {format_duration(w.duration_s)}")
        print(f"  total elapsed: {format_duration(result.duration_s)}")
        if orch.fleet_tags:
            print("\n── fleet version pins ──")
            for name, tag in orch.fleet_tags.items():
                print(f"  {tag.pin}  (git tag {tag.tag} @ {tag.sha[:7]})")
    rates = Rates.from_config(cfg.get("pricing"))
    fleet_size = int(cfg.get("fleet_size") or 0) or None
    print(
        "\n"
        + console_table(result, rates=rates, fleet_size=fleet_size)
        + "\n"
    )

    html_doc = render_html(
        result,
        title="Python 3.14 Migration Fleet",
        subtitle="Standardize tooling (copier · uv · just) + upgrade 3.11 → 3.14",
        waves=[[r.name for r in w.repos] for w in schedule.waves],
        rates=rates,
        fleet_size=fleet_size,
    )
    args.out.write_text(html_doc)
    print(f"Dashboard written to {args.out}")

    usage = write_usage_log(
        result,
        args.usage_out,
        mode=("dry-run" if args.dry_run else "live"),
        model=cfg.get("model"),
        fleet_size=fleet_size,
        rates=rates,
    )
    print(f"Usage log written to {args.usage_out}")
    if usage.get("receipt"):
        print("\n" + format_receipt(usage["receipt"]) + "\n")
    elif usage["repos_priced"] and not usage["buckets_reported"]:
        print(
            "  warning: GET /usage returned totalTokens but no per-bucket split; "
            "cannot compute spend"
        )
    elif not usage["repos_priced"]:
        print("  (no usage data — blocked/errored fleet or /usage unavailable)")

    if hasattr(client, "aclose"):
        await client.aclose()
    if tag_publisher is not None and hasattr(tag_publisher, "aclose"):
        await tag_publisher.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
