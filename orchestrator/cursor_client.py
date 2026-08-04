"""Clients for the Cursor Cloud Agents REST API.

Two implementations behind one interface:

  * RestCursorClient  -> real calls to https://api.cursor.com/v0
  * MockCursorClient  -> deterministic simulation for local dev + demo dry-runs
                         (no API key, no credits burned)

The interface is intentionally tiny: launch an agent, poll an agent. Everything
else the orchestrator needs (concurrency, gates, reporting) is built on top of
these two calls.

REST shape (confirmed against the Cloud Agents API):
  POST /v0/agents          body: {prompt:{text}, source:{repository, ref}, model?, target?}
  GET  /v0/agents/{id}     -> {id, status, target:{prUrl,...}, summary, ...}
Auth: HTTP Basic, API key ("key_...") as the username, empty password.
"""
from __future__ import annotations

import abc
import asyncio
from typing import Any, Optional

import httpx

from .models import RepoTarget

API_BASE = "https://api.cursor.com"

# Cursor status strings -> whether the agent has finished working.
_FINISHED = {"FINISHED", "COMPLETED", "SUCCEEDED"}
_FAILED = {"ERROR", "FAILED", "CANCELLED", "EXPIRED"}


class CursorClient(abc.ABC):
    """Minimal interface the orchestrator depends on."""

    @abc.abstractmethod
    async def launch(self, target: RepoTarget, prompt: str, model: Optional[str]) -> str:
        """Create an agent for `target`; return its agent id."""

    @abc.abstractmethod
    async def poll(self, agent_id: str) -> dict[str, Any]:
        """Return a normalized dict: {done: bool, ok: bool, pr_url, summary, raw}."""


class RestCursorClient(CursorClient):
    def __init__(self, api_key: str, *, environment: Optional[str] = None):
        if not api_key:
            raise ValueError("CURSOR_API_KEY is required for live runs")
        self._environment = environment
        # API key as basic-auth username, empty password.
        self._http = httpx.AsyncClient(
            base_url=API_BASE,
            auth=(api_key, ""),
            timeout=httpx.Timeout(30.0),
            headers={"Content-Type": "application/json"},
        )

    async def launch(self, target: RepoTarget, prompt: str, model: Optional[str]) -> str:
        body: dict[str, Any] = {
            "prompt": {"text": prompt},
            "source": {"repository": target.url, "ref": target.ref},
            # Open a PR rather than pushing to a branch silently: humans review.
            "target": {"autoCreatePr": True},
        }
        if model:
            body["model"] = model
        if self._environment:
            # Custom environment (Dockerfile) with uv/just/copier preinstalled.
            body["source"]["environment"] = self._environment
        resp = await self._http.post("/v0/agents", json=body)
        resp.raise_for_status()
        return resp.json()["id"]

    async def poll(self, agent_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"/v0/agents/{agent_id}")
        resp.raise_for_status()
        data = resp.json()
        status = str(data.get("status", "")).upper()
        done = status in _FINISHED or status in _FAILED
        return {
            "done": done,
            "ok": status in _FINISHED,
            "pr_url": (data.get("target") or {}).get("prUrl"),
            "summary": data.get("summary", ""),
            "raw": data,
        }

    async def aclose(self) -> None:
        await self._http.aclose()


class MockCursorClient(CursorClient):
    """Simulates the agent lifecycle deterministically.

    Drives a realistic demo without an API key:
      * each agent 'runs' for a few polls, then finishes
      * one repo (configurable) finishes but leaves a dependency unresolved,
        so it lands in NEEDS_REVIEW -- the realistic 'not everything is green'
        story that makes the human-review gate matter.
    """

    def __init__(self, *, polls_to_finish: int = 2, flaky_repo: Optional[str] = None):
        self._polls_to_finish = polls_to_finish
        self._flaky_repo = flaky_repo
        self._state: dict[str, dict[str, Any]] = {}
        self._seq = 0

    async def launch(self, target: RepoTarget, prompt: str, model: Optional[str]) -> str:
        self._seq += 1
        agent_id = f"bc_mock{self._seq:03d}"
        self._state[agent_id] = {"target": target, "polls": 0}
        await asyncio.sleep(0.05)  # mimic network latency
        return agent_id

    async def poll(self, agent_id: str) -> dict[str, Any]:
        st = self._state[agent_id]
        st["polls"] += 1
        await asyncio.sleep(0.05)
        if st["polls"] < self._polls_to_finish:
            return {"done": False, "ok": False, "pr_url": None, "summary": "", "raw": {}}
        target: RepoTarget = st["target"]
        flaky = self._flaky_repo and target.name == self._flaky_repo
        pr_num = 40 + self._seq
        return {
            "done": True,
            "ok": True,
            "pr_url": f"{target.url}/pull/{pr_num}",
            "summary": (
                "Applied copier template, converted Makefile->justfile, moved to uv, "
                "bumped to 3.14. "
                + (
                    "Could not resolve pinned transitive dep (pydantic 1.x); left for review."
                    if flaky
                    else "uv lock resolved; test suite green."
                )
            ),
            # `raw` mirrors the shape a live scanner/API would return. A repo that
            # couldn't drop its old pins still carries a known-CVE advisory.
            "raw": {
                "mock": True,
                "flaky": bool(flaky),
                "known_cves": ["CVE-2024-3772 (pydantic <2.0)"] if flaky else [],
            },
        }
