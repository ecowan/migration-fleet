"""Rehearsed live-extension: coach a stuck agent with a follow-up run.

Uses POST /v1/agents/{id}/runs (client.followup) — the durable agent keeps its
workspace across runs, so a targeted follow-up continues where it left off. This
maps directly onto what the interviewers said they test: "discuss and extend it
from a conversation."

THE LIVE MOVE:
  When a repo lands in NEEDS_REVIEW, instead of leaving it for a human, send the
  agent a targeted instruction and re-check. Paste `repair_needs_review` into the
  orchestrator (or run this loop against a single agent) and re-run.

WHAT TO SAY:
  "The v1 API models a durable agent plus per-prompt runs — the workspace persists
   between runs. So triage doesn't have to end at 'needs review'. Watch: I take the
   repo that couldn't resolve its pin and send the agent one coaching follow-up —
   'migrate pydantic to v2 first, then re-lock' — and it continues in the same
   workspace and comes back green."

Reference loop (single agent):
"""
from __future__ import annotations

from orchestrator.gates import DEFAULT_GATES


async def repair_needs_review(client, agent_id, instruction, *, poll, max_polls=30):
    """Send one coaching follow-up to a stuck agent and re-evaluate its gates.

    `poll` is a coroutine like `client.poll`. Returns (passed: bool, checks).
    """
    await client.followup(agent_id, instruction)
    result = None
    for _ in range(max_polls):
        result = await poll(agent_id)
        if result["done"]:
            break
    checks = [gate(result) for gate in DEFAULT_GATES]
    return all(c.passed for c in checks), checks


# Example instruction to keep in your pocket for the demo:
PYDANTIC_FIX = (
    "uv lock failed on the pinned pydantic 1.x transitive dep. "
    "Migrate pydantic v1 -> v2 (class Config -> model_config, validators, .dict()/.json() "
    "call sites) first, then re-run `uv lock` and the test suite."
)
