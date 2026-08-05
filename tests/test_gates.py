"""Gate behaviour — especially the failure modes prose matching used to have.

The original `tests_green` / `deps_resolved` gates substring-matched the agent's
narrative summary and were wrong in both directions: "tests are not green"
passed, "no unresolved dependencies remain" failed. They also failed *open* —
an empty summary passed `deps_resolved`.

These tests pin the replacement contract:
  1. only an explicit `fleet-verify:` line can produce a PASS
  2. narrated failures veto a verdict that claims success
  3. everything else fails closed
"""
from __future__ import annotations

import pytest

from orchestrator.gates import (
    DEFAULT_GATES,
    deps_resolved_gate,
    make_upstream_pins_gate,
    pr_opened_gate,
)
# Aliased: the real name starts with "test", so pytest would collect the gate
# itself as a test case and then fail looking for a `poll` fixture.
from orchestrator.gates import tests_green_gate as green_gate

OK = "fleet-verify: tests=pass deps=resolved"


def poll(summary: str = "", **kw):
    return {"summary": summary, "raw": kw.pop("raw", {}), **kw}


# --- the regressions that motivated the rewrite ------------------------------

@pytest.mark.parametrize(
    "summary",
    [
        "tests are not green",
        "I could not get the tests green",
        "the test suite fails on 3.14",
        "left the tests red for now",
    ],
)
def test_narrated_test_failure_never_passes(summary):
    """Previously: 'tests are not green' PASSED because it contains 'green'."""
    assert green_gate(poll(summary)).passed is False


@pytest.mark.parametrize(
    "summary",
    [
        "no unresolved dependencies remain",
        "all dependencies resolved cleanly",
    ],
)
def test_prose_alone_never_passes_deps(summary):
    """Prose cannot green-light a gate, in either direction. Fail closed."""
    assert deps_resolved_gate(poll(summary)).passed is False


@pytest.mark.parametrize("gate", [green_gate, deps_resolved_gate])
@pytest.mark.parametrize("summary", ["", "   ", "everything worked great, shipped it"])
def test_absent_verdict_fails_closed(gate, summary):
    """Previously: an empty summary PASSED deps_resolved (fail-open)."""
    result = gate(poll(summary))
    assert result.passed is False
    assert "fleet-verify" in result.detail


# --- the passing contract ----------------------------------------------------

def test_explicit_verdict_passes_both():
    assert green_gate(poll(OK)).passed is True
    assert deps_resolved_gate(poll(OK)).passed is True


def test_verdict_is_case_and_space_tolerant():
    assert green_gate(poll("FLEET-VERIFY:  tests = pass   deps = resolved")).passed


def test_verdict_found_amid_normal_prose():
    summary = f"Applied template, moved to uv, bumped to 3.14. {OK}"
    assert green_gate(poll(summary)).passed is True


def test_explicit_failure_verdict_fails():
    summary = "fleet-verify: tests=fail deps=unresolved"
    assert green_gate(poll(summary)).passed is False
    assert deps_resolved_gate(poll(summary)).passed is False


def test_partial_verdict_fails_the_missing_key_only():
    result_deps = deps_resolved_gate(poll("fleet-verify: deps=resolved"))
    result_tests = green_gate(poll("fleet-verify: deps=resolved"))
    assert result_deps.passed is True
    assert result_tests.passed is False
    assert "missing tests=" in result_tests.detail


# --- prose veto: narrated failure outranks a verdict claiming success --------

def test_narrated_failure_vetoes_a_success_verdict():
    """An agent that says it failed but encodes success is not trusted."""
    summary = f"Could not resolve pydantic 1.x for 3.14. {OK}"
    assert deps_resolved_gate(poll(summary)).passed is False


def test_veto_is_scoped_to_its_own_gate():
    """A dependency failure must not knock out the tests gate, and vice versa.

    This is the risk-scoring case: tests can legitimately pass while a
    transitive pin stays unresolved.
    """
    summary = f"Could not resolve pydantic 1.x for 3.14. {OK}"
    assert green_gate(poll(summary)).passed is True


# --- the structural gates (unchanged, but pinned) ---------------------------

def test_pr_opened_gate():
    assert pr_opened_gate(poll(pr_url="https://github.com/o/r/pull/1")).passed is True
    assert pr_opened_gate(poll()).passed is False


def test_upstream_pins_accepts_both_forms():
    gate = make_upstream_pins_gate({"common-utils": "0.0.1.dev0"})
    assert gate(poll("pinned common-utils@0.0.1.dev0")).passed is True
    assert gate(poll("common-utils==0.0.1.dev0 in pyproject")).passed is True
    assert gate(poll("bumped common-utils")).passed is False


def test_upstream_pins_vacuously_true_when_none_required():
    assert make_upstream_pins_gate({})(poll()).passed is True


def test_default_gate_set_is_all_red_on_empty_input():
    """A silent agent gets nothing for free."""
    assert [g(poll()).passed for g in DEFAULT_GATES] == [False, False, False]
