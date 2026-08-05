"""Clients for the Cursor Cloud Agents API (v1).

The v1 API models work as a durable **agent** + per-prompt **runs**: creating an
agent enqueues an initial run, and status / PR / result live on the *run*
(`GET /v1/agents/{id}/runs/{runId}`), fetched via the agent's latest run id.

Two implementations behind one interface:
  * RestCursorClient  -> real calls to https://api.cursor.com/v1
  * MockCursorClient  -> deterministic simulation for local dev + demo dry-runs

Interface (all the orchestrator needs):
  launch(target, prompt, model) -> agent_id      POST /v1/agents
  poll(agent_id)                -> normalized     GET  /v1/agents/{id}/runs/{runId}
  usage(agent_id)               -> token usage    GET  /v1/agents/{id}/usage
  followup(agent_id, prompt)    -> new run         POST /v1/agents/{id}/runs
  cancel(agent_id)              -> stop run        POST /v1/agents/{id}/runs/{runId}/cancel
  list_artifacts(agent_id)      -> artifact metas  GET  /v1/agents/{id}/artifacts

Auth: HTTP Basic, API key ("key_...") as username, empty password.
"""
from __future__ import annotations

import abc
import asyncio
import re
import uuid
from typing import Any, Optional

import httpx

from .models import RepoTarget
from .retries import RetryLogFn, with_retries

API_BASE = "https://api.cursor.com"
API_VERSION = "v1"          # v0 is legacy; v1 is the current durable-agent surface

_FINISHED = {"FINISHED", "COMPLETED", "SUCCEEDED"}
_FAILED = {"ERROR", "FAILED", "CANCELLED", "EXPIRED"}


class CursorClient(abc.ABC):
    @abc.abstractmethod
    async def launch(self, target: RepoTarget, prompt: str, model: Optional[str]) -> str: ...

    @abc.abstractmethod
    async def poll(self, agent_id: str) -> dict[str, Any]:
        """Return {done, ok, pr_url, summary, raw}."""

    @abc.abstractmethod
    async def usage(self, agent_id: str) -> dict[str, Any]:
        """Return the usage response body.

        Implementations may return either the raw body (with token counts nested
        under `totalUsage`) or a flat token dict; AgentRun handles both shapes.
        """

    @abc.abstractmethod
    async def followup(self, agent_id: str, prompt: str) -> None:
        """Send a follow-up prompt as a new run on the same durable agent."""

    @abc.abstractmethod
    async def cancel(self, agent_id: str) -> None:
        """Cancel the agent's active run (terminal; continue via a new run)."""

    @abc.abstractmethod
    async def list_artifacts(self, agent_id: str) -> list[dict[str, Any]]:
        """Return artifact metadata ({path, sizeBytes, updatedAt}, …)."""


class RestCursorClient(CursorClient):
    def __init__(
        self,
        api_key: str,
        *,
        environment: Optional[str] = None,
        max_attempts: int = 6,
        on_retry: Optional[RetryLogFn] = None,
    ):
        if not api_key:
            raise ValueError("CURSOR_API_KEY is required for live runs")
        self._environment = environment
        self._max_attempts = max_attempts
        self._on_retry = on_retry
        self._runs: dict[str, str] = {}          # agent_id -> current run_id
        self._http = httpx.AsyncClient(
            base_url=API_BASE,
            auth=(api_key, ""),
            timeout=httpx.Timeout(30.0),
            headers={"Content-Type": "application/json"},
        )

    def _v(self, path: str) -> str:
        return f"/{API_VERSION}{path}"

    async def launch(self, target: RepoTarget, prompt: str, model: Optional[str]) -> str:
        # Client-supplied id makes create idempotent across transport retries:
        # re-POSTing the same agentId returns 409 agent_id_conflict instead of a duplicate.
        agent_id = f"bc-{uuid.uuid4()}"
        body: dict[str, Any] = {
            "agentId": agent_id,
            "prompt": {"text": prompt},
            "repos": [{"url": target.url, "startingRef": target.ref}],
            "autoCreatePR": True,        # open a PR for human review, don't push silently
            "name": target.name[:100],
        }
        if model:
            body["model"] = {"id": model}
        # Custom environments supply the VM image/tooling; repos still select which
        # GitHub repo to clone. (Docs call named env ↔ repos mutually exclusive when
        # the environment already binds a repo — fleet launches need both.)
        if self._environment:
            body["env"] = {"type": "cloud", "name": self._environment}

        async def _post() -> str:
            resp = await self._http.post(self._v("/agents"), json=body)
            if resp.status_code == 409:
                # Idempotent replay: agent already exists under our agentId.
                return await self._adopt_existing(agent_id)
            resp.raise_for_status()
            data = resp.json()
            # v1 create returns {agent: {...}, run: {...}}; tolerate a flat agent shape too.
            agent = data.get("agent") or data
            run = data.get("run") or {}
            self._runs[agent_id] = (
                agent.get("latestRunId") or run.get("id") or data.get("latestRunId")
            )
            return agent_id

        return await with_retries(
            _post,
            max_attempts=self._max_attempts,
            on_retry=self._on_retry,
            label=f"POST /agents ({target.name})",
        )

    async def _adopt_existing(self, agent_id: str) -> str:
        async def _get() -> str:
            a = await self._http.get(self._v(f"/agents/{agent_id}"))
            a.raise_for_status()
            self._runs[agent_id] = a.json().get("latestRunId")
            return agent_id

        return await with_retries(
            _get,
            max_attempts=self._max_attempts,
            on_retry=self._on_retry,
            label=f"GET /agents/{agent_id} (after 409)",
        )

    async def _current_run(self, agent_id: str) -> str:
        run_id = self._runs.get(agent_id)
        if not run_id:
            async def _get_agent() -> str:
                a = await self._http.get(self._v(f"/agents/{agent_id}"))
                a.raise_for_status()
                rid = a.json().get("latestRunId")
                self._runs[agent_id] = rid
                return rid

            run_id = await with_retries(
                _get_agent,
                max_attempts=self._max_attempts,
                on_retry=self._on_retry,
                label=f"GET /agents/{agent_id}",
            )
        return run_id

    async def poll(self, agent_id: str) -> dict[str, Any]:
        run_id = await self._current_run(agent_id)

        async def _get_run() -> dict[str, Any]:
            resp = await self._http.get(self._v(f"/agents/{agent_id}/runs/{run_id}"))
            resp.raise_for_status()
            data = resp.json()
            status = str(data.get("status", "")).upper()
            pr_url = None
            branch_name = None
            for branch in (data.get("git") or {}).get("branches", []):
                if not branch_name and branch.get("branch"):
                    branch_name = branch["branch"]
                if branch.get("prUrl"):
                    pr_url = branch["prUrl"]
                    if not branch_name:
                        branch_name = branch.get("branch")
                    break
            return {
                "done": status in _FINISHED or status in _FAILED,
                "ok": status in _FINISHED,
                "status": status or "UNKNOWN",
                "pr_url": pr_url,
                "branch": branch_name,
                "summary": data.get("result", ""),
                "duration_ms": data.get("durationMs"),
                "raw": data,
            }

        return await with_retries(
            _get_run,
            max_attempts=self._max_attempts,
            on_retry=self._on_retry,
            label=f"GET /agents/{agent_id}/runs/{run_id}",
        )

    async def usage(self, agent_id: str) -> dict[str, Any]:
        async def _get() -> dict[str, Any]:
            resp = await self._http.get(self._v(f"/agents/{agent_id}/usage"))
            resp.raise_for_status()
            # Return the whole body, not just totalUsage. Anything Cursor reports
            # alongside the token buckets (a cost field, a rate multiplier, a plan
            # tier) is otherwise dropped here and unrecoverable after the process
            # exits. AgentRun._usage_root() unwraps totalUsage for token reads.
            body = resp.json()
            return body if isinstance(body, dict) else {"totalUsage": body}

        return await with_retries(
            _get,
            max_attempts=self._max_attempts,
            on_retry=self._on_retry,
            label=f"GET /agents/{agent_id}/usage",
        )

    async def followup(self, agent_id: str, prompt: str) -> None:
        async def _post() -> None:
            resp = await self._http.post(
                self._v(f"/agents/{agent_id}/runs"), json={"prompt": {"text": prompt}}
            )
            resp.raise_for_status()
            data = resp.json()
            self._runs[agent_id] = (data.get("run") or {}).get("id") or data.get("id")

        await with_retries(
            _post,
            max_attempts=self._max_attempts,
            on_retry=self._on_retry,
            label=f"POST /agents/{agent_id}/runs",
        )

    async def cancel(self, agent_id: str) -> None:
        run_id = await self._current_run(agent_id)

        async def _post() -> None:
            resp = await self._http.post(
                self._v(f"/agents/{agent_id}/runs/{run_id}/cancel")
            )
            # Already-terminal runs return 409 run_not_cancellable — treat as done.
            if resp.status_code == 409:
                return
            resp.raise_for_status()

        await with_retries(
            _post,
            max_attempts=self._max_attempts,
            on_retry=self._on_retry,
            label=f"POST /agents/{agent_id}/runs/{run_id}/cancel",
        )

    async def list_artifacts(self, agent_id: str) -> list[dict[str, Any]]:
        async def _get() -> list[dict[str, Any]]:
            resp = await self._http.get(self._v(f"/agents/{agent_id}/artifacts"))
            resp.raise_for_status()
            return list(resp.json().get("items") or [])

        return await with_retries(
            _get,
            max_attempts=self._max_attempts,
            on_retry=self._on_retry,
            label=f"GET /agents/{agent_id}/artifacts",
        )

    async def aclose(self) -> None:
        await self._http.aclose()


# Fleet version pins as rendered into a consumer's prompt by
# tags.render_pins_prompt(). Used by the mock to echo pins back the way a real
# agent would after writing them into pyproject.toml (name@0.0.1.dev0).
_PIN_REF = re.compile(
    r"\b([A-Za-z0-9][A-Za-z0-9._-]*)@(0\.0\.1\.dev[0-9]+)\b"
)


class MockCursorClient(CursorClient):
    """Deterministic simulation: agents 'run' for a few polls then finish; one repo
    lands in NEEDS_REVIEW; a follow-up 'repairs' it. Fabricates token usage so the
    cost story is demoable without spending anything.

    `transient_launch_failures` maps repo name → how many consecutive launch
    attempts should raise a fake 429 before succeeding (exercises retry paths).
    """

    def __init__(
        self,
        *,
        polls_to_finish: int = 2,
        flaky_repo: Optional[str] = None,
        transient_launch_failures: Optional[dict[str, int]] = None,
    ):
        self._polls_to_finish = polls_to_finish
        self._flaky_repo = flaky_repo
        self._transient_launch_failures = dict(transient_launch_failures or {})
        self._launch_attempts: dict[str, int] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._seq = 0

    async def launch(self, target: RepoTarget, prompt: str, model: Optional[str]) -> str:
        attempts = self._launch_attempts.get(target.name, 0) + 1
        self._launch_attempts[target.name] = attempts
        # A real agent that followed the playbook writes upstream version pins
        # into pyproject.toml and mentions them in its summary — which is what
        # upstream_pins_gate looks for. Mirror that here, or every consumer
        # fails a gate in dry-run and the fleet-coherence story reads as broken.
        pins = sorted({f"{m.group(1)}@{m.group(2)}" for m in _PIN_REF.finditer(prompt or "")})
        fail_n = self._transient_launch_failures.get(target.name, 0)
        if attempts <= fail_n:
            req = httpx.Request("POST", "https://api.cursor.com/v1/agents")
            resp = httpx.Response(429, request=req, text='{"error":"Too Many Requests"}')
            raise httpx.HTTPStatusError("429 Too Many Requests", request=req, response=resp)
        self._seq += 1
        agent_id = f"bc-{uuid.uuid4()}"
        self._state[agent_id] = {
            "target": target,
            "polls": 0,
            "repaired": False,
            "cancelled": False,
            "run_id": f"run-mock{self._seq:03d}",
            "pins": pins,
        }
        await asyncio.sleep(0.05)
        return agent_id

    def _is_flaky(self, st: dict[str, Any]) -> bool:
        return bool(self._flaky_repo and st["target"].name == self._flaky_repo
                    and not st["repaired"])

    async def poll(self, agent_id: str) -> dict[str, Any]:
        st = self._state[agent_id]
        if st.get("cancelled"):
            return {
                "done": True, "ok": False, "status": "CANCELLED",
                "pr_url": None, "branch": None, "summary": "cancelled",
                "duration_ms": 0, "raw": {"status": "CANCELLED", "mock": True},
            }
        st["polls"] += 1
        await asyncio.sleep(0.05)
        if st["polls"] < self._polls_to_finish:
            return {
                "done": False, "ok": False, "status": "RUNNING",
                "pr_url": None, "branch": None, "summary": "",
                "duration_ms": None, "raw": {"status": "RUNNING"},
            }
        flaky = self._is_flaky(st)
        target: RepoTarget = st["target"]
        pr_num = 40 + self._seq
        pins: list[str] = st.get("pins") or []
        # Note the flaky repo still reports its pins: it *did* pin its upstream,
        # it just couldn't resolve its own transitive pydantic dep. Keeping those
        # failures separate is what makes the NEEDS_REVIEW story precise on stage.
        pin_note = (
            " Pinned upstream fleet deps by version: "
            + ", ".join(pins)
            + "."
        ) if pins else ""
        return {
            "done": True, "ok": True, "status": "FINISHED",
            "pr_url": f"{target.url}/pull/{pr_num}",
            "branch": f"cursor/migrate-{target.name}",
            "summary": (
                "Applied copier template, converted Makefile->justfile, moved to uv, "
                "bumped to 3.14. "
                + ("Could not resolve pinned transitive dep (pydantic 1.x); left for review."
                   if flaky else "uv lock resolved; test suite green.")
                + pin_note
            ),
            "duration_ms": 12_000 + self._seq * 1_000,
            "raw": {"mock": True, "flaky": flaky, "status": "FINISHED",
                    "upstream_pins": pins,
                    "known_cves": ["CVE-2024-3772 (pydantic <2.0)"] if flaky else []},
        }

    async def usage(self, agent_id: str) -> dict[str, Any]:
        st = self._state[agent_id]
        name = st["target"].name
        base = 18_000 + (sum(ord(c) for c in name) % 12) * 1_500
        if self._flaky_repo and name == self._flaky_repo:
            base *= 3          # the gnarly repo burns more tokens (retries on the pin)
        if st.get("repaired"):
            base = int(base * 1.35)  # follow-up run adds usage
        inp, out = base, base // 4
        cw, cr = base * 2, base * 3
        # Match live GET /v1/agents/{id}/usage shape: buckets live under totalUsage.
        return {
            "totalUsage": {
                "inputTokens": inp,
                "outputTokens": out,
                "cacheWriteTokens": cw,
                "cacheReadTokens": cr,
                "totalTokens": inp + out + cw + cr,
            },
            "runs": [
                {
                    "id": st.get("run_id") or "run-mock",
                    "usage": {
                        "inputTokens": inp,
                        "outputTokens": out,
                        "cacheWriteTokens": cw,
                        "cacheReadTokens": cr,
                        "totalTokens": inp + out + cw + cr,
                    },
                }
            ],
        }

    async def followup(self, agent_id: str, prompt: str) -> None:
        st = self._state[agent_id]
        st["polls"] = 0          # new run
        st["repaired"] = True    # the coaching follow-up resolves the blocker
        st["cancelled"] = False
        await asyncio.sleep(0.05)

    async def cancel(self, agent_id: str) -> None:
        st = self._state[agent_id]
        st["cancelled"] = True
        await asyncio.sleep(0.01)

    async def list_artifacts(self, agent_id: str) -> list[dict[str, Any]]:
        st = self._state[agent_id]
        name = st["target"].name
        flaky = self._is_flaky(st)
        items = [
            {
                "path": "artifacts/migration-summary.md",
                "sizeBytes": 2400 + len(name) * 10,
                "updatedAt": "2026-04-13T18:45:00.000Z",
            },
        ]
        if not flaky:
            items.append(
                {
                    "path": "artifacts/pytest-junit.xml",
                    "sizeBytes": 1800,
                    "updatedAt": "2026-04-13T18:45:00.000Z",
                }
            )
        return items
