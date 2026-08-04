"""Verification gates.

A gate inspects a finished agent result and returns a CheckResult. Gates are the
'trust' layer: the difference between "an agent changed some files" and "a
migration you can actually merge". They're deliberately a simple list so you can
add one live during the interview (e.g. a policy gate).

In a live run these would shell out / call GitHub checks against the agent's PR.
Here they read the normalized poll payload so the whole thing runs in dry-run.
"""
from __future__ import annotations

from typing import Any, Callable

from .models import CheckResult

# A gate: (poll_result) -> CheckResult
Gate = Callable[[dict[str, Any]], CheckResult]


def pr_opened_gate(poll: dict[str, Any]) -> CheckResult:
    ok = bool(poll.get("pr_url"))
    return CheckResult("pr_opened", ok, poll.get("pr_url") or "no PR url returned")


def deps_resolved_gate(poll: dict[str, Any]) -> CheckResult:
    # The agent reports unresolved deps in its summary; a live version would parse
    # `uv lock` output or the PR's CI status instead.
    summary = (poll.get("summary") or "").lower()
    unresolved = "could not resolve" in summary or "unresolved" in summary
    return CheckResult(
        "deps_resolved",
        not unresolved,
        "dependency resolution incomplete" if unresolved else "uv lock resolved",
    )


def tests_green_gate(poll: dict[str, Any]) -> CheckResult:
    summary = (poll.get("summary") or "").lower()
    green = "green" in summary or "passed" in summary
    return CheckResult(
        "tests_green",
        green,
        "test suite green" if green else "tests not confirmed green",
    )


def make_upstream_pins_gate(required_tags: dict[str, str]) -> Gate:
    """Require the agent to have pinned each upstream `cursor.dev/…` tag.

    `required_tags` maps package name → tag ref (e.g. common-utils → cursor.dev/abc1234).
    """
    def upstream_pins_gate(poll: dict[str, Any]) -> CheckResult:
        if not required_tags:
            return CheckResult("upstream_pins", True, "no upstream fleet pins required")
        blob = " ".join(
            [
                poll.get("summary") or "",
                str(poll.get("raw") or {}),
            ]
        )
        missing = [
            f"{name}@{tag}"
            for name, tag in required_tags.items()
            if tag not in blob
        ]
        if missing:
            return CheckResult(
                "upstream_pins",
                False,
                "missing pin(s): " + ", ".join(missing),
            )
        return CheckResult(
            "upstream_pins",
            True,
            "pinned " + ", ".join(f"{n}@{t}" for n, t in required_tags.items()),
        )

    return upstream_pins_gate


# Default gate set. Order is display order.
DEFAULT_GATES: list[Gate] = [pr_opened_gate, deps_resolved_gate, tests_green_gate]
