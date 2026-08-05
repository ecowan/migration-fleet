"""Fleet orchestrator.

Fans out one migration agent per repo with bounded concurrency, polls each to
completion, runs verification gates against the result, and classifies the
outcome (DONE / NEEDS_REVIEW / ERROR).

Design seams (useful for the live-extension segment):
  * `gates`  -> add/remove verification without touching the run loop
  * `client` -> swap Mock for REST without touching anything else
  * `concurrency` -> one knob for "run 3" vs "run 300"
  * `tag_publisher` -> after a lib is DONE, publish 0.0.1.dev0 tag for consumers
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from orchestrator.router import Router

from .cursor_client import CursorClient
from .gates import DEFAULT_GATES, Gate, make_upstream_pins_gate
from .models import AgentRun, FleetResult, RepoTarget, Status, WaveTiming
from .retries import is_retryable_error
from .scheduler import Schedule, Wave
from .tags import DevTag, TagPublisher, render_pins_prompt

ProgressFn = Callable[[AgentRun], None]
WaveFn = Callable[[Wave, list[RepoTarget], list[AgentRun]], None]
# Step narration for --verbose: (repo_name, message) -> None
StepFn = Callable[[str, str], None]


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
        wave_retries: int = 2,
        wave_retry_delay: float = 5.0,
        tag_publisher: Optional[TagPublisher] = None,
        router: Optional[Router] = None,
        on_progress: Optional[ProgressFn] = None,
        on_wave: Optional[WaveFn] = None,
        on_step: Optional[StepFn] = None,
    ):
        self._client = client
        self._prompt = prompt
        self._model = model
        self._router = router
        self._gates = gates if gates is not None else list(DEFAULT_GATES)
        self._sem = asyncio.Semaphore(concurrency)
        self._poll_interval = poll_interval
        self._max_polls = max_polls
        self._wave_retries = wave_retries
        self._wave_retry_delay = wave_retry_delay
        self._tag_publisher = tag_publisher
        self._fleet_tags: dict[str, DevTag] = {}
        self._on_progress = on_progress or (lambda run: None)
        self._on_wave = on_wave
        self._on_step = on_step or (lambda _repo, _msg: None)

    @property
    def fleet_tags(self) -> dict[str, DevTag]:
        return dict(self._fleet_tags)

    def _step(self, repo: str, message: str) -> None:
        self._on_step(repo, message)

    def _prompt_for(self, target: RepoTarget) -> tuple[str, dict[str, str]]:
        """Base playbook + required upstream version pins for this repo."""
        required = {
            name: self._fleet_tags[name].version
            for name in target.depends_on
            if name in self._fleet_tags
        }
        pins = {name: self._fleet_tags[name] for name in required}
        return self._prompt + render_pins_prompt(pins), required

    def _model_for(self, target: RepoTarget) -> Optional[str]:
        """Per-repo model from the optional router; else the fleet-wide default."""
        if self._router is not None:
            return self._router.route(target)
        return self._model

    async def run_one(self, target: RepoTarget) -> AgentRun:
        run = AgentRun(target=target)
        name = target.name
        t0 = time.perf_counter()
        prompt, required_pins = self._prompt_for(target)
        gates = list(self._gates)
        if required_pins:
            gates.append(make_upstream_pins_gate(required_pins))
            self._step(
                name,
                "upstream pins required: "
                + ", ".join(f"{n}@{t}" for n, t in required_pins.items()),
            )

        model = self._model_for(target)
        run.model = model
        if self._router is not None:
            c = self._router.assess(target)
            self._step(
                name,
                f"router score={c.score} → model {model}"
                + (f" ({', '.join(c.reasons)})" if c.reasons else ""),
            )

        self._step(name, "waiting for a concurrency slot…")
        async with self._sem:
            try:
                self._step(
                    name,
                    f"POST /agents — launching Cloud Agent"
                    + (f" (model {model})" if model else ""),
                )
                self._step(name, f"repository {target.url} @ {target.ref}")
                run.agent_id = await self._client.launch(target, prompt, model)
                self._step(name, f"agent created → {run.agent_id}")
                run.status = Status.RUNNING
                self._on_progress(run)

                poll = None
                for attempt in range(1, self._max_polls + 1):
                    self._step(
                        name,
                        f"poll {attempt}/{self._max_polls} — GET agent run status…",
                    )
                    poll = await self._client.poll(run.agent_id)
                    agent_status = (
                        poll.get("status")
                        or str((poll.get("raw") or {}).get("status") or "?")
                    )
                    elapsed = format_duration(time.perf_counter() - t0)
                    if poll["done"]:
                        outcome = "succeeded" if poll["ok"] else "failed"
                        self._step(
                            name,
                            f"poll {attempt}: status={agent_status} → {outcome} "
                            f"(elapsed {elapsed})",
                        )
                        break
                    self._step(
                        name,
                        f"poll {attempt}: status={agent_status} — still working "
                        f"(elapsed {elapsed}; next check in {self._poll_interval:g}s)",
                    )
                    await asyncio.sleep(self._poll_interval)

                if poll is None or not poll["done"]:
                    run.status = Status.ERROR
                    run.error = "timed out waiting for agent"
                    self._step(
                        name,
                        f"gave up after {self._max_polls} polls — cancelling active run",
                    )
                    await self._cancel_quiet(run)
                    await self._enrich(run)
                    run.duration_s = time.perf_counter() - t0
                    self._on_progress(run)
                    return run

                if not poll["ok"]:
                    run.status = Status.ERROR
                    run.error = poll.get("summary") or "agent reported failure"
                    self._step(name, f"agent reported failure: {run.error}")
                    await self._enrich(run)
                    run.duration_s = time.perf_counter() - t0
                    self._on_progress(run)
                    return run

                # Agent finished -> record result and run gates.
                run.pr_url = poll.get("pr_url")
                run.summary = poll.get("summary", "")
                self._step(name, "agent finished — running verification gates")
                if run.pr_url:
                    self._step(name, f"PR url: {run.pr_url}")
                else:
                    self._step(name, "PR url: (none yet)")
                run.checks = []
                for gate in gates:
                    check = gate(poll)
                    run.checks.append(check)
                    mark = "PASS" if check.passed else "FAIL"
                    detail = f" — {check.detail}" if check.detail else ""
                    self._step(name, f"gate {check.name}: {mark}{detail}")
                run.status = Status.DONE if run.gates_passed else Status.NEEDS_REVIEW
                self._step(name, f"classified → {run.status.value}")
                await self._enrich(run)

                # Libs: publish 0.0.1.dev0 on the PR head for downstream pins.
                await self._maybe_publish_tag(run)

            except Exception as exc:  # noqa: BLE001 - surface any failure per-repo
                run.status = Status.ERROR
                run.error = f"{type(exc).__name__}: {exc}"
                self._step(name, f"exception: {run.error}")
                await self._enrich(run)

        run.duration_s = time.perf_counter() - t0
        self._on_progress(run)
        return run

    async def _maybe_publish_tag(self, run: AgentRun) -> None:
        """Publish fleet version tag when this repo is a DONE lib; log the outcome."""
        name = run.target.name
        if run.status is not Status.DONE or not run.target.publish_tag:
            return
        if not self._tag_publisher:
            self._step(
                name,
                "tag skip — no tag publisher (set GITHUB_TOKEN for live runs)",
            )
            return
        if not run.pr_url:
            self._step(name, "tag skip — no PR url to resolve a head commit")
            return

        self._step(name, f"publishing fleet version tag on PR head ({run.pr_url})…")
        try:
            dev = await self._tag_publisher.publish(
                name=name, url=run.target.url, pr_url=run.pr_url
            )
            self._fleet_tags[name] = dev
            run.dev_tag = dev.pin
            self._step(
                name,
                f"tagged {dev.tag} @ {dev.sha[:7]} → pin {dev.pin} ({dev.pep508})",
            )
        except Exception as exc:  # noqa: BLE001
            run.status = Status.ERROR
            run.error = f"tag publish failed: {type(exc).__name__}: {exc}"
            self._step(name, run.error)

    async def _cancel_quiet(self, run: AgentRun) -> None:
        if not run.agent_id:
            return
        try:
            await self._client.cancel(run.agent_id)
            self._step(run.target.name, f"cancelled run on {run.agent_id}")
        except Exception as exc:  # noqa: BLE001
            self._step(
                run.target.name,
                f"cancel failed ({type(exc).__name__}: {exc}) — leaving for dashboard",
            )

    async def _enrich(self, run: AgentRun) -> None:
        """Pull usage + artifacts once the agent is terminal (best-effort)."""
        if not run.agent_id:
            return
        name = run.target.name
        try:
            self._step(name, f"GET /agents/{run.agent_id}/usage — token telemetry")
            run.usage = await self._client.usage(run.agent_id)
            toks = run.total_tokens
            self._step(name, f"usage → {toks:,} tokens")
        except Exception as exc:  # noqa: BLE001
            self._step(name, f"usage unavailable: {type(exc).__name__}: {exc}")
        try:
            self._step(name, f"GET /agents/{run.agent_id}/artifacts — listing outputs")
            run.artifacts = await self._client.list_artifacts(run.agent_id)
            if run.artifacts:
                paths = ", ".join(
                    str(a.get("path") or "?") for a in run.artifacts[:4]
                )
                more = f" (+{len(run.artifacts) - 4})" if len(run.artifacts) > 4 else ""
                self._step(name, f"artifacts → {paths}{more}")
            else:
                self._step(name, "artifacts → (none)")
        except Exception as exc:  # noqa: BLE001
            self._step(name, f"artifacts unavailable: {type(exc).__name__}: {exc}")

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
                # Consumers that need a fleet tag also wait until it exists.
                missing_tags = [
                    d for d in repo.depends_on
                    if d not in self._fleet_tags
                    and d in results
                    and results[d].target.publish_tag
                    and results[d].status is Status.DONE
                ]
                # If upstream was DONE but tag missing, treat as unclean.
                # (publish_tag failure already flips status to ERROR above.)
                if block_on_upstream and (bad or missing_tags):
                    why = bad or missing_tags
                    run = AgentRun(
                        target=repo,
                        status=Status.BLOCKED,
                        error=f"upstream not clean: {', '.join(why)}",
                        duration_s=0.0,
                    )
                    results[repo.name] = run
                    blocked.append(run)
                else:
                    runnable.append(repo)

            if self._on_wave:
                self._on_wave(wave, runnable, blocked)
            for run in blocked:
                self._step(
                    run.target.name,
                    f"skipped — {run.error}",
                )
                self._on_progress(run)

            if runnable:
                self._step(
                    "*",
                    f"wave {wave.index + 1}: starting "
                    + ", ".join(r.name for r in runnable),
                )
                if self._fleet_tags:
                    self._step(
                        "*",
                        "fleet tags available: "
                        + ", ".join(t.pin for t in self._fleet_tags.values()),
                    )
            wave_runs = await asyncio.gather(*(self.run_one(r) for r in runnable))
            for run in wave_runs:
                results[run.target.name] = run

            # Re-queue repos that died on transient API errors (429/5xx/timeout)
            # before we let block_on_upstream freeze their consumers.
            for pass_i in range(1, self._wave_retries + 1):
                retryable = [
                    r for r in wave_runs
                    if r.status is Status.ERROR and is_retryable_error(r.error)
                ]
                if not retryable:
                    break
                names = ", ".join(r.target.name for r in retryable)
                self._step(
                    "*",
                    f"wave {wave.index + 1}: transient API error on {names} — "
                    f"retry pass {pass_i}/{self._wave_retries} "
                    f"in {self._wave_retry_delay:g}s",
                )
                await asyncio.sleep(self._wave_retry_delay)
                retried = await asyncio.gather(
                    *(self.run_one(r.target) for r in retryable)
                )
                by_name = {r.target.name: r for r in retried}
                wave_runs = [by_name.get(r.target.name, r) for r in wave_runs]
                for run in retried:
                    results[run.target.name] = run

            wave_dur = time.perf_counter() - wave_t0
            self._step(
                "*",
                f"wave {wave.index + 1} complete in {format_duration(wave_dur)}",
            )
            wave_timings.append(
                WaveTiming(
                    index=wave.index,
                    duration_s=wave_dur,
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
