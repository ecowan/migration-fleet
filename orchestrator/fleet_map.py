"""Fleet map — deps, merge waves, latest GitHub tags/PRs, and how they connect.

Read-only overview for the console (and `just map`). Local checkouts supply the
import graph; GitHub supplies tags + PRs when a token is available.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx
import yaml

from .dep_matrix import DepMatrix, build_dep_matrix
from .models import RepoTarget
from .scheduler import Schedule, build_schedule
from .tags import FLEET_DEV_VERSION, parse_github_repo, short_sha


@dataclass
class TagInfo:
    name: str
    sha: str
    url: str = ""


@dataclass
class PrInfo:
    number: int
    title: str
    state: str          # OPEN / MERGED / CLOSED
    head_sha: str
    head_ref: str
    url: str


@dataclass
class RepoSnapshot:
    target: RepoTarget
    wave: int                     # 1-based merge wave
    imports: list[str]            # upstream fleet deps
    dependents: list[str]         # who imports this repo
    fleet_tag: Optional[TagInfo] = None
    recent_tags: list[TagInfo] = field(default_factory=list)
    latest_pr: Optional[PrInfo] = None
    open_prs: list[PrInfo] = field(default_factory=list)
    tag_pr: Optional[PrInfo] = None   # open/merged PR whose head matches fleet tag
    error: str = ""


@dataclass
class FleetMap:
    snapshots: list[RepoSnapshot]
    matrix: DepMatrix
    schedule: Schedule
    github_ok: bool
    github_note: str = ""


def _github_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if tok.strip():
        return tok.strip()
    try:
        out = subprocess.check_output(
            ["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL
        )
        return out.strip()
    except Exception:  # noqa: BLE001
        return ""


def load_targets(config_path: Path) -> list[RepoTarget]:
    with open(config_path) as fh:
        cfg = yaml.safe_load(fh)
    targets = []
    for raw in cfg["repos"]:
        row = dict(raw)
        row.pop("depends_on", None)
        row.pop("root", None)
        targets.append(RepoTarget(**row))
    return targets


def _tag_url(owner: str, repo: str, name: str) -> str:
    return f"https://github.com/{owner}/{repo}/releases/tag/{name}"


def _lookup_tag(
    http: httpx.Client, owner: str, repo: str, name: str
) -> Optional[TagInfo]:
    """Resolve one tag by name (covers tags outside the newest-N listing)."""
    try:
        resp = http.get(f"/repos/{owner}/{repo}/git/ref/tags/{name}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            if not data:
                return None
            data = data[0]
        obj = data["object"]
        sha = obj["sha"]
        # Annotated tags point at a tag object; peel to the commit SHA.
        if obj.get("type") == "tag":
            tresp = http.get(f"/repos/{owner}/{repo}/git/tags/{sha}")
            tresp.raise_for_status()
            sha = tresp.json()["object"]["sha"]
        return TagInfo(name=name, sha=sha, url=_tag_url(owner, repo, name))
    except Exception:  # noqa: BLE001
        return None


def _fetch_repo_github(
    http: httpx.Client, owner: str, repo: str
) -> tuple[list[TagInfo], list[PrInfo], Optional[str]]:
    """Return (tags newest-first, recent PRs, error)."""
    tags: list[TagInfo] = []
    prs: list[PrInfo] = []
    try:
        tresp = http.get(f"/repos/{owner}/{repo}/tags", params={"per_page": 8})
        tresp.raise_for_status()
        for row in tresp.json():
            sha = row["commit"]["sha"]
            tags.append(
                TagInfo(
                    name=row["name"],
                    sha=sha,
                    url=_tag_url(owner, repo, row["name"]),
                )
            )
        # Newest-N can miss the fleet pin once other tags pile up.
        if not any(t.name == FLEET_DEV_VERSION for t in tags):
            fleet = _lookup_tag(http, owner, repo, FLEET_DEV_VERSION)
            if fleet is not None:
                tags.append(fleet)
    except Exception as exc:  # noqa: BLE001
        return [], [], f"tags: {type(exc).__name__}: {exc}"

    try:
        # Mixed open + recently updated so we can match a tag to a PR head.
        presp = http.get(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "all", "sort": "updated", "direction": "desc", "per_page": 12},
        )
        presp.raise_for_status()
        for row in presp.json():
            state = "MERGED" if row.get("merged_at") else str(row["state"]).upper()
            prs.append(
                PrInfo(
                    number=row["number"],
                    title=(row.get("title") or "")[:72],
                    state=state,
                    head_sha=row["head"]["sha"],
                    head_ref=row["head"]["ref"],
                    url=row["html_url"],
                )
            )
    except Exception as exc:  # noqa: BLE001
        return tags, [], f"prs: {type(exc).__name__}: {exc}"

    return tags, prs, None


def build_fleet_map(
    *,
    config_path: Path,
    targets_root: Path,
    token: Optional[str] = None,
) -> FleetMap:
    targets = load_targets(config_path)
    matrix = build_dep_matrix(targets, roots=targets_root)
    targets = matrix.apply(targets)
    schedule = build_schedule(targets)

    wave_of = {
        r.name: w.index + 1
        for w in schedule.waves
        for r in w.repos
    }
    by_name = {t.name: t for t in targets}
    dependents: dict[str, list[str]] = {t.name: [] for t in targets}
    for consumer, ups in matrix.depends_on.items():
        for u in ups:
            dependents.setdefault(u, []).append(consumer)
    for name in dependents:
        dependents[name] = sorted(
            dependents[name], key=lambda n: (wave_of.get(n, 99), n)
        )

    tok = token if token is not None else _github_token()
    github_ok = bool(tok)
    github_note = "" if github_ok else "no GitHub token (set GITHUB_TOKEN or `gh auth login`)"

    snapshots: list[RepoSnapshot] = []
    http: Optional[httpx.Client] = None
    if github_ok:
        http = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(30.0),
        )

    try:
        # Emit in merge (wave) order.
        ordered = [by_name[n] for n in schedule.order if n in by_name]
        for t in ordered:
            snap = RepoSnapshot(
                target=t,
                wave=wave_of.get(t.name, 0),
                imports=list(matrix.depends_on.get(t.name, [])),
                dependents=dependents.get(t.name, []),
            )
            if http is not None:
                try:
                    owner, repo = parse_github_repo(t.url)
                except ValueError as exc:
                    snap.error = str(exc)
                    snapshots.append(snap)
                    continue
                tags, prs, err = _fetch_repo_github(http, owner, repo)
                if err:
                    snap.error = err
                snap.recent_tags = tags
                for tag in tags:
                    if tag.name == FLEET_DEV_VERSION:
                        snap.fleet_tag = tag
                        break
                snap.open_prs = [p for p in prs if p.state == "OPEN"]
                snap.latest_pr = prs[0] if prs else None
                if snap.fleet_tag:
                    for p in prs:
                        if p.head_sha.lower() == snap.fleet_tag.sha.lower():
                            snap.tag_pr = p
                            break
            snapshots.append(snap)
    finally:
        if http is not None:
            http.close()

    return FleetMap(
        snapshots=snapshots,
        matrix=matrix,
        schedule=schedule,
        github_ok=github_ok,
        github_note=github_note,
    )


# ── rendering ──────────────────────────────────────────────────────────────


def _pad(s: str, width: int) -> str:
    s = s if len(s) <= width else s[: width - 1] + "…"
    return f"{s:<{width}}"


def _tag_cell(s: RepoSnapshot) -> str:
    if s.fleet_tag:
        return f"{s.fleet_tag.name}@{short_sha(s.fleet_tag.sha)}"
    if s.target.publish_tag:
        return "(none yet)"
    return "—"


def _pr_cell(s: RepoSnapshot) -> str:
    if s.tag_pr:
        return f"#{s.tag_pr.number} {s.tag_pr.state} ← tag"
    if s.latest_pr:
        return f"#{s.latest_pr.number} {s.latest_pr.state}"
    return "—"


def render_repo_table(m: FleetMap) -> str:
    headers = ("REPO", "WAVE", "IMPORTS", "USED BY", "PUBLISH", "FLEET TAG", "TAG / LATEST PR")
    rows = []
    for s in m.snapshots:
        rows.append(
            (
                s.target.name,
                str(s.wave),
                ", ".join(s.imports) or "—",
                ", ".join(s.dependents) or "—",
                "yes" if s.target.publish_tag else "no",
                _tag_cell(s),
                _pr_cell(s),
            )
        )
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], min(len(cell), 36))
    # Cap a few columns so the table fits a typical terminal.
    widths[2] = min(widths[2], 28)
    widths[3] = min(widths[3], 28)
    widths[5] = min(widths[5], 24)
    widths[6] = min(widths[6], 22)

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(_pad(c, widths[i]) for i, c in enumerate(row))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(r) for r in rows)
    return "\n".join(lines)


def render_edge_table(m: FleetMap) -> str:
    lines = [
        f"{'CONSUMER':<22}  {'UPSTREAM':<22}  REASON",
        f"{'-'*22}  {'-'*22}  {'-'*40}",
    ]
    if not m.matrix.edges:
        lines.append("(no in-fleet edges)")
        return "\n".join(lines)
    seen: set[tuple[str, str]] = set()
    for consumer, upstream, reason in m.matrix.edges:
        key = (consumer, upstream)
        if key in seen:
            # Collapse duplicate declared+import evidence onto one row with both.
            continue
        reasons = [
            r for c, u, r in m.matrix.edges if c == consumer and u == upstream
        ]
        seen.add(key)
        lines.append(
            f"{consumer:<22}  {upstream:<22}  {'; '.join(reasons)}"
        )
    return "\n".join(lines)


def render_merge_order(m: FleetMap) -> str:
    lines = []
    last = len(m.schedule.waves)
    for w in m.schedule.waves:
        names = ", ".join(r.name for r in w.repos)
        hint = ""
        if w.index == 0:
            pubs = [r.name for r in w.repos if r.publish_tag]
            if pubs:
                hint = f"  ← merge first; publishes {' / '.join(f'{p}@{FLEET_DEV_VERSION}' for p in pubs)}"
            else:
                hint = "  ← merge first"
        elif w.index == last - 1 and last > 1:
            hint = "  ← merge last (after upstreams land)"
        if w.note:
            hint = f"  ← {w.note}"
        lines.append(f"  wave {w.index + 1}: {names}{hint}")
    if m.schedule.cycle_nodes:
        lines.append(
            "  !! cycle: " + ", ".join(m.schedule.cycle_nodes)
        )
    return "\n".join(lines)


def render_import_ascii(m: FleetMap) -> str:
    """ASCII digraph: arrow means 'imports / depends on' (consumer → upstream)."""
    lines = ["  (arrow = imports / depends on)"]
    edges = sorted({(c, u) for c, u, _ in m.matrix.edges})
    if not edges:
        lines.append("  (no edges)")
        return "\n".join(lines)
    for c, u in edges:
        lines.append(f"  {c} ──► {u}")
    return "\n".join(lines)


def render_import_mermaid(m: FleetMap) -> str:
    """Mermaid flowchart: nodes clustered by merge wave; edges = imports."""
    lines = [
        "```mermaid",
        "flowchart BT",
    ]
    for w in m.schedule.waves:
        label = f"wave {w.index + 1} — merge order"
        lines.append(f"  subgraph w{w.index}[\"{label}\"]")
        for r in w.repos:
            safe = r.name.replace("-", "_")
            pin = ""
            snap = next((s for s in m.snapshots if s.target.name == r.name), None)
            if snap and snap.fleet_tag:
                pin = f"<br/>{FLEET_DEV_VERSION}"
            elif r.publish_tag:
                pin = f"<br/>tag pending"
            lines.append(f"    {safe}[\"{r.name}{pin}\"]")
        lines.append("  end")
    seen: set[tuple[str, str]] = set()
    for c, u, _ in m.matrix.edges:
        if (c, u) in seen:
            continue
        seen.add((c, u))
        lines.append(
            f"  {c.replace('-', '_')} --> {u.replace('-', '_')}"
        )
    lines.append("```")
    return "\n".join(lines)


def render_pin_graph(m: FleetMap) -> str:
    """Who should pin whose fleet tag after upstream libs merge."""
    lines = ["  (lib tag ──pin──► consumers that must take it)"]
    any_pin = False
    for s in m.snapshots:
        if not s.target.publish_tag:
            continue
        if not s.dependents:
            continue
        any_pin = True
        tag = (
            f"{s.target.name}@{s.fleet_tag.name}"
            if s.fleet_tag
            else f"{s.target.name}@{FLEET_DEV_VERSION} (not on remote yet)"
        )
        for dep in s.dependents:
            lines.append(f"  {tag} ──pin──► {dep}")
    if not any_pin:
        lines.append("  (no publish_tag libs with in-fleet consumers)")
    return "\n".join(lines)


def render_tag_pr_detail(m: FleetMap) -> str:
    lines = []
    for s in m.snapshots:
        if not (s.fleet_tag or s.open_prs or s.error):
            continue
        lines.append(f"  {s.target.name}")
        if s.error:
            lines.append(f"    ! {s.error}")
        if s.fleet_tag:
            link = s.tag_pr.url if s.tag_pr else s.target.url
            pr_bit = (
                f" = PR #{s.tag_pr.number} ({s.tag_pr.state})"
                if s.tag_pr
                else " (no matching PR head in recent list)"
            )
            lines.append(
                f"    tag  {s.fleet_tag.name} @ {short_sha(s.fleet_tag.sha)}{pr_bit}"
            )
            if s.tag_pr:
                lines.append(f"         {s.tag_pr.url}")
        # Other recent tags (skip the fleet one already shown).
        extras = [
            t for t in s.recent_tags
            if not s.fleet_tag or t.name != s.fleet_tag.name
        ][:3]
        if extras:
            bits = ", ".join(f"{t.name}@{short_sha(t.sha)}" for t in extras)
            lines.append(f"    also {bits}")
        if s.open_prs:
            for p in s.open_prs[:3]:
                mark = " ★ tag" if s.tag_pr and p.number == s.tag_pr.number else ""
                lines.append(
                    f"    pr   #{p.number} [{p.state}] {p.head_ref} @ {short_sha(p.head_sha)}{mark}"
                )
                lines.append(f"         {p.url}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_fleet_map(m: FleetMap) -> str:
    parts = [
        "── Fleet map ──",
        "",
        "Repos",
        render_repo_table(m),
        "",
        "── Edges (what imports what) ──",
        render_edge_table(m),
        "",
        "── Merge order (topological waves) ──",
        render_merge_order(m),
        "",
        "── Directed graph · imports ──",
        render_import_ascii(m),
        "",
        "── Directed graph · fleet pins ──",
        render_pin_graph(m),
        "",
        "── Mermaid (paste into any mermaid renderer) ──",
        render_import_mermaid(m),
    ]
    if m.github_ok:
        detail = render_tag_pr_detail(m)
        if detail:
            parts += ["", "── Tags ↔ PRs ──", detail]
    else:
        parts += ["", f"── Tags ↔ PRs ──", f"  skipped — {m.github_note}"]
    parts.append("")
    return "\n".join(parts)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="Show fleet deps, merge order, tags, and PRs")
    p.add_argument("--config", type=Path, default=here / "repos.yaml")
    p.add_argument("--targets-root", type=Path, default=here / "targets")
    p.add_argument(
        "--json",
        action="store_true",
        help="machine-readable dump (no graphs)",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="skip GitHub (local matrix + waves only)",
    )
    args = p.parse_args(argv)

    m = build_fleet_map(
        config_path=args.config,
        targets_root=args.targets_root,
        token="" if args.offline else None,
    )

    if args.json:
        payload = {
            "github_ok": m.github_ok,
            "waves": [
                {"index": w.index + 1, "repos": [r.name for r in w.repos], "note": w.note}
                for w in m.schedule.waves
            ],
            "edges": [
                {"consumer": c, "upstream": u, "reason": r}
                for c, u, r in m.matrix.edges
            ],
            "repos": [
                {
                    "name": s.target.name,
                    "url": s.target.url,
                    "wave": s.wave,
                    "imports": s.imports,
                    "dependents": s.dependents,
                    "publish_tag": s.target.publish_tag,
                    "fleet_tag": (
                        {"name": s.fleet_tag.name, "sha": s.fleet_tag.sha}
                        if s.fleet_tag
                        else None
                    ),
                    "tag_pr": (
                        {
                            "number": s.tag_pr.number,
                            "state": s.tag_pr.state,
                            "url": s.tag_pr.url,
                            "head_sha": s.tag_pr.head_sha,
                        }
                        if s.tag_pr
                        else None
                    ),
                    "latest_pr": (
                        {
                            "number": s.latest_pr.number,
                            "state": s.latest_pr.state,
                            "url": s.latest_pr.url,
                        }
                        if s.latest_pr
                        else None
                    ),
                    "open_prs": [
                        {"number": p.number, "state": p.state, "url": p.url}
                        for p in s.open_prs
                    ],
                    "error": s.error or None,
                }
                for s in m.snapshots
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(render_fleet_map(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
