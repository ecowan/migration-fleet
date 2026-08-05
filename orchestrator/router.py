"""Optional model router — pick a model from local repo complexity.

When wired into ``FleetOrchestrator(router=...)``, each launch uses
``router.route(target)`` instead of the fleet-wide default model. Omit the
router (or pass ``None``) and behavior is unchanged.

Score is deterministic from the checkout + dep matrix — no API calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .dep_matrix import DepMatrix
from .models import RepoTarget

# Requirement pins that make a Python modernization meaningfully harder.
# Group 1 = package name. Matches requirements.txt and quoted setup.py entries.
_BREAKING = (
    re.compile(r"""['"]?(pydantic)['"]?\s*(==|<=|>=|~=|<|!=)\s*['"]?1\.""", re.I),
    re.compile(r"""['"]?(django)['"]?\s*(==|<=|>=|~=|<|!=)\s*['"]?[12]\.""", re.I),
    re.compile(r"""['"]?(flask)['"]?\s*(==|<=|>=|~=|<|!=)\s*['"]?1\.""", re.I),
)

_SKIP_DIR = {
    ".git",
    ".venv",
    "venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "__pycache__",
    "node_modules",
    ".tox",
}


@dataclass(frozen=True)
class Complexity:
    """Explainable score used for routing (handy in --verbose)."""
    name: str
    loc: int
    breaking_pins: tuple[str, ...]
    n_dependents: int
    n_upstream: int
    publish_tag: bool
    score: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelTiers:
    """Model ids per difficulty band. Override when you have distinct models."""
    easy: str = "composer-2.5"
    mid: str = "composer-2.5"
    hard: str = "composer-2.5"
    easy_max: int = 3   # score <= easy_max → easy
    mid_max: int = 7    # score <= mid_max → mid; else hard


class Router:
    """Map a ``RepoTarget`` to a model id from LOC + complexity signals."""

    def __init__(
        self,
        *,
        roots: Path,
        matrix: Optional[DepMatrix] = None,
        tiers: Optional[ModelTiers] = None,
        default_model: str = "composer-2.5",
    ):
        self._roots = Path(roots)
        self._matrix = matrix
        self._tiers = tiers or ModelTiers(
            easy=default_model, mid=default_model, hard=default_model
        )
        self._default = default_model
        self._dependents = _dependents_index(matrix) if matrix else {}
        self._cache: dict[str, Complexity] = {}

    def loc(self, target: RepoTarget) -> int:
        return self.assess(target).loc

    def difficulty(self, target: RepoTarget) -> int:
        return self.assess(target).score

    def assess(self, target: RepoTarget) -> Complexity:
        cached = self._cache.get(target.name)
        if cached is not None:
            return cached
        c = _assess(target, self._roots, self._dependents)
        self._cache[target.name] = c
        return c

    def route(self, target: RepoTarget) -> str:
        """Return the model id for this repo."""
        score = self.difficulty(target)
        t = self._tiers
        if score <= t.easy_max:
            return t.easy
        if score <= t.mid_max:
            return t.mid
        return t.hard

    def summary_lines(self) -> list[str]:
        """Human-readable routing table (call after assessing, or pass targets)."""
        lines = []
        for name in sorted(self._cache):
            c = self._cache[name]
            # Re-build a synthetic target-less route from cached score.
            model = (
                self._tiers.easy
                if c.score <= self._tiers.easy_max
                else self._tiers.mid
                if c.score <= self._tiers.mid_max
                else self._tiers.hard
            )
            why = f"  ({', '.join(c.reasons)})" if c.reasons else ""
            lines.append(
                f"  {name}: score={c.score} → {model}  "
                f"[loc={c.loc}, breaking={len(c.breaking_pins)}, "
                f"deps_in={c.n_dependents}, upstream={c.n_upstream}]{why}"
            )
        return lines


def router_from_matrix(
    matrix: DepMatrix,
    targets: list[RepoTarget],
    *,
    roots: Path,
    default_model: str = "composer-2.5",
    tiers: Optional[ModelTiers] = None,
) -> Router:
    """Build a router and pre-warm assessments for ``targets``."""
    r = Router(
        roots=roots,
        matrix=matrix,
        tiers=tiers,
        default_model=default_model,
    )
    for t in targets:
        r.assess(t)
    return r


def _dependents_index(matrix: DepMatrix) -> dict[str, int]:
    counts: dict[str, int] = {name: 0 for name in matrix.depends_on}
    for consumer, ups in matrix.depends_on.items():
        for u in ups:
            counts[u] = counts.get(u, 0) + 1
    return counts


def _checkout(target: RepoTarget, roots: Path) -> Path:
    # Prefer explicit target.root (fleet checkouts dir); fall back to roots.
    base = target.root if target.root and target.root != Path.cwd() else roots
    return Path(base) / target.name


def _assess(
    target: RepoTarget,
    roots: Path,
    dependents: dict[str, int],
) -> Complexity:
    root = _checkout(target, roots)
    loc = _count_loc(root)
    breaking = tuple(_breaking_pins(root))
    n_dep = dependents.get(target.name, 0)
    n_up = len(target.depends_on)
    reasons: list[str] = []

    score = min(loc // 500, 5)
    if loc:
        reasons.append(f"loc={loc}")

    if breaking:
        score += 3 * len(breaking)
        reasons.append("breaking:" + ",".join(breaking))

    if n_dep:
        score += min(n_dep, 4)
        reasons.append(f"{n_dep} dependents")

    if target.publish_tag:
        score += 2
        reasons.append("publish_tag")

    if n_up:
        score += min(n_up, 2)
        reasons.append(f"{n_up} upstream")

    return Complexity(
        name=target.name,
        loc=loc,
        breaking_pins=breaking,
        n_dependents=n_dep,
        n_upstream=n_up,
        publish_tag=target.publish_tag,
        score=score,
        reasons=tuple(reasons),
    )


def _count_loc(root: Path) -> int:
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*.py"):
        if any(p in _SKIP_DIR for p in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        total += sum(
            1
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return total


def _breaking_pins(root: Path) -> list[str]:
    """Return short labels for known-hard dependency pins in the checkout."""
    if not root.is_dir():
        return []
    found: list[str] = []
    seen: set[str] = set()
    files = list(root.glob("requirements*.txt"))
    for extra in ("setup.py", "pyproject.toml"):
        path = root / extra
        if path.is_file():
            files.append(path)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            for pat in _BREAKING:
                m = pat.search(line)
                if not m:
                    continue
                pkg = m.group(1).lower()
                if pkg not in seen:
                    seen.add(pkg)
                    found.append(pkg)
    return found
