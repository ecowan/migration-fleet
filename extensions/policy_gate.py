"""Rehearsed live-extension: a compliance policy gate.

THE LIVE MOVE (what you type on stage):
  1. paste `no_known_cves_gate` below into orchestrator/gates.py
  2. append it to DEFAULT_GATES
  3. re-run `python run.py --dry-run`

That's it — the new gate shows up as a column across the whole fleet, and
risk-scoring (which couldn't drop its old pins) now fails a *security* gate too.

WHAT TO SAY:
  "In a regulated shop, 'tests pass' isn't the bar — compliance wants guarantees.
   The nice thing about the gate abstraction is I can encode an org policy as code
   and it applies across all 120 repos at once. Watch: I add one gate that fails
   any repo still carrying a known-CVE dependency..."

This file is a reference so you can practice and confirm it works; in the demo you
type the function into gates.py live to show the seam.
"""
from __future__ import annotations

from typing import Any

from orchestrator.models import CheckResult


def no_known_cves_gate(poll: dict[str, Any]) -> CheckResult:
    """Fail a repo whose result still carries a known-CVE dependency.

    Reads the advisory list off the poll payload. In a live run this is where you
    would call your SCA scanner (or read the PR's security-check status) instead.
    """
    cves = (poll.get("raw") or {}).get("known_cves") or []
    return CheckResult(
        name="no_known_cves",
        passed=not cves,
        detail="; ".join(cves) if cves else "no known CVEs",
    )


# To wire it in gates.py:
#     from .models import CheckResult
#     def no_known_cves_gate(poll): ...        # (body above, using CheckResult directly)
#     DEFAULT_GATES.append(no_known_cves_gate)
