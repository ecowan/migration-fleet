"""Fleet orchestrator.

Fans out one migration agent per repo with bounded concurrency, polls each to
completion, runs verification gates against the result, and classifies the
outcome (DONE / NEEDS_REVIEW / ERROR).

Design seams (useful for the live-extension segment):
  * `gates`  -> add/remove verification without touching the run loop
  * `client` -> swap Mock for REST without touching anything else
  * `concurrency` -> one knob for "run 3" vs "run 300"
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from .cursor_client import CursorClient
from .gates import DEFAULT_GATES, Gate
from .models import AgentRun, FleetResult, RepoTarget, Status, WaveTiming
from .scheduler import Schedule, Wave

ProgressFn = Callable[[AgentRun], None]
WaveFn = Callable[[Wave, list[RepoTarget], list[AgentRun]], None]


class FleetOrchestrator:
    def __init__(
        self,
        client: CursorClient,
        prompt: str,
        *,
        model: Optional[str] = None,
        gates: Optional[list[Gate]] = None,
        concurrency: int = 5,
        poll_interval: float = 2.0,
        max_polls: int = 300,
        on_progress: Optional[ProgressFn] = None,
        on_wave: Optional[WaveFn] = None,
    ):
        self._client = client
        self._prompt = prompt
        self._model = model
        self._gates = gates if gates is not None else list(DEFAULT_GATES)
        self._sem = asyncio.Semaphore(concurrency)
        self._poll_interval = poll_interval
        self._max_polls = max_polls
        self._on_progress = on_progress or (lambda run: None)
        self._on_wave = on_wave

    async def run_one(self, target: RepoTarget) -> AgentRun:
        run = AgentRun(target=target)
        t0 = time.perf_counter()
        async with self._sem:
            try:
                run.agent_id = await self._client.launch(target, self._prompt, self._model)
                run.status = Status.RUNNING
                self._on_progress(run)

                poll = None
                for _ in range(self._max_polls):
                    poll = await self._client.poll(run.agent_id)
                    if poll["done"]:
                        break
                    await asyncio.sleep(self._poll_interval)

                if poll is None or not poll["done"]:
                    run.status = Status.ERROR
                    run.error = "timed out waiting for agent"
                    run.duration_s = time.perf_counter() - t0
                    self._on_progress(run)
                    return run

                if not poll["ok"]:
                    run.status = Status.ERROR
                    run.error = poll.get("summary") or "agent reported failure"
                    run.duration_s = time.perf_counter() - t0
                    self._on_progress(run)
                    return run

                # Agent finished -> record result and run gates.
                run.pr_url = poll.get("pr_url")
                run.summary = poll.get("summary", "")
                run.checks = [gate(poll) for gate in self._gates]
                run.status = Status.DONE if run.gates_passed else Status.NEEDS_REVIEW

            except Exception as exc:  # noqa: BLE001 - surface any failure per-repo
                run.status = Status.ERROR
                run.error = f"{type(exc).__name__}: {exc}"

        run.duration_s = time.perf_counter() - t0
        self._on_progress(run)
        return run

    async def run(self, targets: list[RepoTarget]) -> FleetResult:
        t0 = time.perf_counter()
        runs = await asyncio.gather(*(self.run_one(t) for t in targets))
        return FleetResult(runs=list(runs), duration_s=time.perf_counter() - t0)

    async def run_scheduled(
        self, schedule: Schedule, *, block_on_upstream: bool = True
    ) -> FleetResult:
        """Run wave by wave. Within a wave, fan out in parallel; between waves, a
        barrier. If `block_on_upstream`, a repo whose in-fleet dependency did not
        reach DONE is marked BLOCKED and never launched — you don't migrate a
        consumer against a dependency that isn't clean.
        """
        t0 = time.perf_counter()
        results: dict[str, AgentRun] = {}
        wave_timings: list[WaveTiming] = []

        for wave in schedule.waves:
            wave_t0 = time.perf_counter()
            runnable, blocked = [], []
            for repo in wave.repos:
                bad = [
                    d for d in repo.depends_on
                    if d in results and results[d].status is not Status.DONE
                ]
                if block_on_upstream and bad:
                    run = AgentRun(
                        target=repo,
                        status=Status.BLOCKED,
                        error=f"upstream not clean: {', '.join(bad)}",
                        duration_s=0.0,
                    )
                    results[repo.name] = run
                    blocked.append(run)
                else:
                    runnable.append(repo)

            if self._on_wave:
                self._on_wave(wave, runnable, blocked)
            for run in blocked:
                self._on_progress(run)

            wave_runs = await asyncio.gather(*(self.run_one(r) for r in runnable))
            for run in wave_runs:
                results[run.target.name] = run

            wave_timings.append(
                WaveTiming(
                    index=wave.index,
                    duration_s=time.perf_counter() - wave_t0,
                    repo_names=[r.name for r in wave.repos],
                )
            )

        # Return in scheduled (topological) order.
        runs = [results[name] for name in schedule.order]
        return FleetResult(
            runs=runs,
            duration_s=time.perf_counter() - t0,
            waves=wave_timings,
        )


def summarize(runs: list[AgentRun]) -> dict[str, int]:
    counts = {s.value: 0 for s in Status if s not in {Status.PENDING, Status.RUNNING}}
    for r in runs:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    counts["total"] = len(runs)
    return counts


def format_duration(seconds: Optional[float]) -> str:
    """Human-readable duration for console/HTML (e.g. 1.2s, 3m 05s)."""
    if seconds is None:
        return "—"
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"
