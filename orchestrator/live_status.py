"""Live terminal status bar for fleet runs.

Reserves the bottom of the TTY for a progress bar + elapsed time counter that
ticks while agents are working. Log lines print above it.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from typing import Optional, TextIO

from .models import AgentRun, Status
from .orchestrator import format_duration

_TERMINAL = {Status.DONE, Status.NEEDS_REVIEW, Status.BLOCKED, Status.ERROR}


class LiveStatusBar:
    """Sticky bottom status bar: `[████░░] 2/4  running: foo  |  elapsed 1m 05s`."""

    def __init__(
        self,
        repo_names: list[str],
        *,
        stream: TextIO | None = None,
        enabled: Optional[bool] = None,
    ):
        self._names = list(repo_names)
        self._statuses: dict[str, Status] = {n: Status.PENDING for n in self._names}
        self._stream = stream or sys.stderr
        self._enabled = (
            sys.stderr.isatty() and sys.stdout.isatty()
            if enabled is None
            else enabled
        )
        self._started_at: Optional[float] = None
        self._wave_label = ""
        self._lines = 2  # rows reserved at bottom
        self._height = 0
        self._drawn = False
        self._ticker: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if not self._enabled:
            return
        self._started_at = time.perf_counter()
        self._height = shutil.get_terminal_size((80, 24)).lines
        # Scroll region: leave bottom rows for the status bar.
        top_end = max(1, self._height - self._lines)
        self._stream.write(f"\033[1;{top_end}r")
        self._stream.write(f"\033[{top_end};1H\n")
        self._stream.flush()
        self._draw()

    async def start_ticker(self, interval: float = 0.25) -> None:
        if not self._enabled:
            return
        self.start()

        async def _tick() -> None:
            while True:
                await asyncio.sleep(interval)
                self._draw()

        self._ticker = asyncio.create_task(_tick())

    async def stop(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            try:
                await self._ticker
            except asyncio.CancelledError:
                pass
            self._ticker = None
        if not self._enabled:
            return
        self._draw(final=True)
        # Reset scroll region and park cursor below the bar.
        self._stream.write("\033[r")
        self._stream.write(f"\033[{self._height};1H\n")
        self._stream.flush()
        self._drawn = False

    def set_wave(self, index: int, names: list[str]) -> None:
        self._wave_label = f"wave {index + 1}: {', '.join(names)}"
        self._draw()

    def update(self, run: AgentRun) -> None:
        self._statuses[run.target.name] = run.status
        self._draw()

    def log(self, message: str = "") -> None:
        """Print a log line above the status bar."""
        if not self._enabled:
            print(message)
            return
        # Plain prints stay inside the scroll region; redraw the sticky bar after.
        sys.stdout.write(message + "\n")
        sys.stdout.flush()
        self._draw()

    def _elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.perf_counter() - self._started_at

    def _bar(self, width: int) -> str:
        total = len(self._names) or 1
        finished = sum(1 for s in self._statuses.values() if s in _TERMINAL)
        filled = int(width * finished / total)
        # Pulse the next cell while work remains.
        running = any(s is Status.RUNNING for s in self._statuses.values())
        body = "█" * filled
        if finished < total:
            pulse = "▓" if running and int(self._elapsed() * 4) % 2 == 0 else "░"
            body += pulse + "░" * max(0, width - filled - 1)
        else:
            body += "░" * (width - filled)
        return f"[{body[:width]}]"

    def _status_line(self) -> tuple[str, str]:
        total = len(self._names)
        finished = sum(1 for s in self._statuses.values() if s in _TERMINAL)
        running = [n for n, s in self._statuses.items() if s is Status.RUNNING]
        cols = shutil.get_terminal_size((80, 24)).columns
        bar_w = max(10, min(28, cols - 48))
        bar = self._bar(bar_w)
        pct = int(100 * finished / total) if total else 100
        left = f"{bar}  {finished}/{total} ({pct}%)"
        if running:
            shown = ", ".join(running[:3])
            if len(running) > 3:
                shown += f" +{len(running) - 3}"
            mid = f"  running: {shown}"
        elif finished < total:
            mid = "  waiting…"
        else:
            mid = "  complete"
        line1 = (left + mid)[: cols - 1]
        elapsed = format_duration(self._elapsed())
        wave = f"  ·  {self._wave_label}" if self._wave_label else ""
        line2 = f"elapsed  {elapsed}{wave}"[: cols - 1]
        return line1, line2

    def _erase(self) -> None:
        if not self._enabled or not self._drawn:
            return
        h = self._height
        self._stream.write("\033[s")
        for row in range(h - self._lines + 1, h + 1):
            self._stream.write(f"\033[{row};1H\033[2K")
        self._stream.write("\033[u")
        self._stream.flush()

    def _draw(self, *, final: bool = False) -> None:
        if not self._enabled:
            return
        # Refresh height in case the terminal was resized.
        self._height = shutil.get_terminal_size((80, 24)).lines
        line1, line2 = self._status_line()
        h = self._height
        self._stream.write("\033[s")
        self._stream.write(f"\033[{h - 1};1H\033[2K\033[0m{line1}")
        self._stream.write(f"\033[{h};1H\033[2K\033[0m{line2}")
        if final:
            # Leave the final bar visible; no cursor restore into scroll area.
            self._stream.write("\n")
        else:
            self._stream.write("\033[u")
        self._stream.flush()
        self._drawn = True
