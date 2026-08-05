"""Verification gates.

A gate inspects a finished agent result and returns a CheckResult. Gates are the
'trust' layer: the difference between "an agent changed some files" and "a
migration you can actually merge". They're deliberately a simple list so you can
add one live during the interview (e.g. a policy gate).

**Gates fail closed.** A gate that cannot establish a positive result returns
False, which lands the repo in NEEDS_REVIEW — where a human looks at it. Silence,
ambiguity and malformed output are all failures, never passes. At fleet scale the
expensive mistake is a false green, not a false review.

Structured verdicts, not prose
------------------------------
`tests_green` and `deps_resolved` do **not** parse the agent's narrative summary.
Free text cannot be matched reliably — "tests are not green" contains the word
"green" — so the playbook requires the agent to emit one machine-readable line:

    fleet-verify: tests=pass deps=resolved

That line is the contract. Prose is used for exactly one thing: recognising an
explicit failure the agent described but forgot to encode, which can only ever
turn a result *red*, never green. So the only path to a passing gate is an
explicit, well-formed verdict.

In a live deployment these would additionally corroborate against the PR's CI
status (GitHub checks) or a junit artifact. That is a stricter source for the
same contract, not a different design.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .models import CheckResult

# A gate: (poll_result) -> CheckResult
Gate = Callable[[dict[str, Any]], CheckResult]

# The machine-readable verdict the playbook asks the agent to emit, e.g.
#   fleet-verify: tests=pass deps=resolved
# Tolerant of surrounding markdown, case, and separator spacing.
_VERDICT_LINE = re.compile(r"fleet-verify\s*[:\-]\s*(?P<body>[^\n\r]+)", re.I)
_VERDICT_KV = re.compile(r"\b(?P<key>[a-z_]+)\s*=\s*(?P<value>[a-z_]+)", re.I)

# Unambiguous failure phrases. These can only ever force a FAIL, so a false
# match costs a needless human review — never a bad merge.
_TESTS_FAILED_PHRASES = (
    "test suite fails",
    "tests fail",
    "tests failed",
    "tests are failing",
    "failing test",
    "test failure",
    "tests not green",
    "tests are not green",
    "could not get the tests",
    "unable to run the test",
    "left the tests red",
)
_DEPS_UNRESOLVED_PHRASES = (
    "could not resolve",
    "couldn't resolve",
    "cannot resolve",
    "unable to resolve",
    "resolution failed",
    "failed to resolve",
    "dependency conflict",
    "unresolved dependenc",
    "unresolved transitive",
    "left the pin unresolved",
)


def _text_of(poll: dict[str, Any]) -> str:
    """Summary plus the raw payload, lowercased — everything the agent gave us."""
    return " ".join([str(poll.get("summary") or ""), str(poll.get("raw") or "")]).lower()


def _verdict(poll: dict[str, Any]) -> dict[str, str]:
    """Parse `fleet-verify: k=v k=v` into a dict. Empty when absent/malformed."""
    match = _VERDICT_LINE.search(_text_of(poll))
    if not match:
        return {}
    return {
        m.group("key").lower(): m.group("value").lower()
        for m in _VERDICT_KV.finditer(match.group("body"))
    }


def _first_phrase(text: str, phrases: tuple[str, ...]) -> Optional[str]:
    for p in phrases:
        if p in text:
            return p
    return None


def _structured_gate(
    poll: dict[str, Any],
    *,
    name: str,
    keys: tuple[str, ...],
    ok_values: tuple[str, ...],
    fail_phrases: tuple[str, ...],
    ok_detail: str,
) -> CheckResult:
    """Shared shape: explicit verdict decides; prose can only veto; absence fails."""
    text = _text_of(poll)

    # Prose veto first — an explicit failure the agent narrated outranks a
    # verdict line that claims success.
    phrase = _first_phrase(text, fail_phrases)
    if phrase is not None:
        return CheckResult(name, False, f"agent reported failure: “{phrase}”")

    verdict = _verdict(poll)
    if not verdict:
        return CheckResult(
            name, False,
            "no `fleet-verify` line in agent output — cannot confirm, failing closed",
        )

    value = next((verdict[k] for k in keys if k in verdict), None)
    if value is None:
        return CheckResult(
            name, False,
            f"`fleet-verify` present but missing {keys[0]}= — failing closed",
        )
    if value in ok_values:
        return CheckResult(name, True, ok_detail)
    return CheckResult(name, False, f"agent reported {keys[0]}={value}")


def pr_opened_gate(poll: dict[str, Any]) -> CheckResult:
    ok = bool(poll.get("pr_url"))
    return CheckResult("pr_opened", ok, poll.get("pr_url") or "no PR url returned")


def deps_resolved_gate(poll: dict[str, Any]) -> CheckResult:
    return _structured_gate(
        poll,
        name="deps_resolved",
        keys=("deps", "dependencies"),
        ok_values=("resolved", "ok", "pass"),
        fail_phrases=_DEPS_UNRESOLVED_PHRASES,
        ok_detail="uv lock resolved",
    )


def tests_green_gate(poll: dict[str, Any]) -> CheckResult:
    return _structured_gate(
        poll,
        name="tests_green",
        keys=("tests", "test"),
        ok_values=("pass", "passed", "green", "ok"),
        fail_phrases=_TESTS_FAILED_PHRASES,
        ok_detail="test suite green",
    )


def make_upstream_pins_gate(required_pins: dict[str, str]) -> Gate:
    """Require the agent to have pinned each upstream fleet version.

    `required_pins` maps package name → version (e.g. common-utils → 0.0.1.dev0).
    Accepts either ``name@version`` or ``name==version`` in the agent summary.
    """
    def upstream_pins_gate(poll: dict[str, Any]) -> CheckResult:
        if not required_pins:
            return CheckResult("upstream_pins", True, "no upstream fleet pins required")
        blob = " ".join(
            [
                poll.get("summary") or "",
                str(poll.get("raw") or {}),
            ]
        )
        missing = []
        for name, version in required_pins.items():
            at_form = f"{name}@{version}"
            eq_form = f"{name}=={version}"
            if at_form not in blob and eq_form not in blob:
                missing.append(at_form)
        if missing:
            return CheckResult(
                "upstream_pins",
                False,
                "missing pin(s): " + ", ".join(missing),
            )
        return CheckResult(
            "upstream_pins",
            True,
            "pinned " + ", ".join(f"{n}@{v}" for n, v in required_pins.items()),
        )

    return upstream_pins_gate


# Default gate set. Order is display order.
DEFAULT_GATES: list[Gate] = [pr_opened_gate, deps_resolved_gate, tests_green_gate]
