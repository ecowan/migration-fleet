"""Dependency-ordered wave scheduling.

Consumes the dependency graph produced by the dependency-matrix skill (X imports
Y imports Z) and orders the fleet so a shared library is migrated and verified
*before* the services that depend on it. Migrating a consumer against an
un-migrated dependency is how you get spurious failures at scale — so we don't.

Output is a list of **waves**. Repos within a wave have no unmet dependency on
each other and run in parallel; waves run in sequence.

  Z (no deps)          -> wave 0
  Y (imports Z)        -> wave 1
  X (imports Y)        -> wave 2

Cycles are a real enterprise occurrence (service A and B import each other). We
detect them rather than hang: the offending repos are grouped into a single wave
and flagged, so a human decides how to break the cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import RepoTarget


@dataclass
class Wave:
    index: int
    repos: list[RepoTarget]
    note: str = ""


@dataclass
class Schedule:
    waves: list[Wave]
    cycle_nodes: list[str] = field(default_factory=list)  # names caught in a cycle

    @property
    def order(self) -> list[str]:
        return [r.name for w in self.waves for r in w.repos]


def build_schedule(targets: list[RepoTarget]) -> Schedule:
    """Kahn's algorithm, grouped by level, with cycle fallback."""
    by_name = {t.name: t for t in targets}

    # Only count dependencies that are actually in this fleet (ignore externals).
    deps: dict[str, set[str]] = {
        t.name: {d for d in t.depends_on if d in by_name} for t in targets
    }
    # dependents[x] = repos that must wait for x.
    dependents: dict[str, set[str]] = {t.name: set() for t in targets}
    for name, ds in deps.items():
        for d in ds:
            dependents[d].add(name)

    indegree = {name: len(ds) for name, ds in deps.items()}
    remaining = set(by_name)
    waves: list[Wave] = []
    idx = 0

    while remaining:
        ready = sorted(n for n in remaining if indegree[n] == 0)
        if not ready:
            # Everything left is in a cycle (or depends on one). Group and flag.
            cyc = sorted(remaining)
            waves.append(
                Wave(idx, [by_name[n] for n in cyc],
                     note="dependency cycle — human must choose a break point")
            )
            return Schedule(waves, cycle_nodes=cyc)

        waves.append(Wave(idx, [by_name[n] for n in ready]))
        for n in ready:
            remaining.remove(n)
            for dep in dependents[n]:
                indegree[dep] -= 1
        idx += 1

    return Schedule(waves)
