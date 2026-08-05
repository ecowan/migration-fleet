"""Core data types shared across the orchestrator.

Kept deliberately small and framework-free so the shapes are easy to reason
about in a demo and easy to extend live (add a field, add a check).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Status(str, Enum):
    PENDING = "pending"        # created locally, not yet launched
    RUNNING = "running"        # agent working in its cloud VM
    NEEDS_REVIEW = "needs_review"  # finished but a gate failed / human decision needed
    DONE = "done"              # finished, all gates green, PR open
    BLOCKED = "blocked"        # not launched: an upstream dependency isn't clean
    ERROR = "error"            # agent or API failure


# Terminal states that stop polling.
TERMINAL = {Status.NEEDS_REVIEW, Status.DONE, Status.BLOCKED, Status.ERROR}


@dataclass
class RepoTarget:
    """One repo in the migration fleet."""
    name: str
    url: str
    ref: str = "main"
    # Names of repos this one imports. Filled by dep_matrix.build_dep_matrix()
    # from each checkout's requirements / setup.py / imports — not from YAML.
    # Semantics: every name listed here must migrate BEFORE this repo.
    depends_on: list[str] = field(default_factory=list)
    # After DONE, tag the PR head as 0.0.1.dev0 for downstream version pins.
    publish_tag: bool = False


@dataclass
class CheckResult:
    """Outcome of one verification gate run against the agent's result."""
    name: str
    passed: bool
    detail: str = ""


@dataclass
class AgentRun:
    """The full lifecycle record for one repo's migration agent."""
    target: RepoTarget
    agent_id: Optional[str] = None
    status: Status = Status.PENDING
    pr_url: Optional[str] = None
    summary: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    error: Optional[str] = None
    # Wall time from launch (or block decision) through terminal status.
    duration_s: Optional[float] = None
    # Published fleet pin, e.g. common-utils@0.0.1.dev0 (libs with publish_tag=True).
    dev_tag: Optional[str] = None
    # Token usage from GET /v1/agents/{id}/usage (totalUsage shape).
    usage: Optional[dict] = None
    # Artifact metadata from GET /v1/agents/{id}/artifacts.
    artifacts: list[dict] = field(default_factory=list)

    @property
    def gates_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def _usage_root(self) -> dict:
        """Token counts, whether `usage` is the raw response body or totalUsage itself.

        RestCursorClient stores the whole JSON body (so fields we don't model yet —
        a cost line, a rate multiplier — survive to the usage log); MockCursorClient
        returns the flat token dict. Accept both.
        """
        if not self.usage:
            return {}
        inner = self.usage.get("totalUsage")
        return inner if isinstance(inner, dict) else self.usage

    def _tok(self, key: str) -> int:
        return int(self._usage_root().get(key) or 0)

    @property
    def input_tokens(self) -> int:
        return self._tok("inputTokens")

    @property
    def output_tokens(self) -> int:
        return self._tok("outputTokens")

    @property
    def cache_write_tokens(self) -> int:
        return self._tok("cacheWriteTokens")

    @property
    def cache_read_tokens(self) -> int:
        return self._tok("cacheReadTokens")

    @property
    def total_tokens(self) -> int:
        # Prefer the API's own total; fall back to summing buckets so a response
        # that omits totalTokens still prices correctly instead of reading as free.
        explicit = self._tok("totalTokens")
        if explicit:
            return explicit
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_tokens
            + self.cache_read_tokens
        )


@dataclass
class WaveTiming:
    """Wall time for one dependency wave (parallel repos share this span)."""
    index: int
    duration_s: float
    repo_names: list[str] = field(default_factory=list)


@dataclass
class FleetResult:
    """Runs plus timing for the full scheduled fleet process."""
    runs: list[AgentRun]
    duration_s: float
    waves: list[WaveTiming] = field(default_factory=list)
