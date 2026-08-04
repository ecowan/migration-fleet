"""Retry helpers for transient Cursor / GitHub API failures.

Used by RestCursorClient (per-call) and FleetOrchestrator (per-wave re-queue).
"""
from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, Optional, TypeVar

import httpx

T = TypeVar("T")

# HTTP statuses that usually mean "try again shortly", not "bad request".
RETRYABLE_STATUS = {429, 502, 503, 504}

# Substrings in exception / AgentRun.error text that mark a retryable failure
# after the client has already exhausted its own attempts.
_RETRYABLE_MARKERS = (
    "429",
    "502",
    "503",
    "504",
    "too many requests",
    "resource_exhausted",
    "rate limit",
    "rate_limited",
    "timeout",
    "timed out",
    "connecterror",
    "remoteprotocolerror",
    "networkerror",
)

RetryLogFn = Callable[[str], None]


def is_retryable_status(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS


def is_retryable_error(message: Optional[str]) -> bool:
    if not message:
        return False
    lower = message.lower()
    return any(m in lower for m in _RETRYABLE_MARKERS)


def retry_after_seconds(resp: httpx.Response, attempt: int, *, base: float = 2.0,
                        cap: float = 60.0) -> float:
    """Prefer Retry-After header; otherwise exponential backoff with jitter."""
    header = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if header:
        try:
            return min(cap, max(1.0, float(header)))
        except ValueError:
            pass
    # attempt is 1-based; 2, 4, 8… capped, plus small jitter
    delay = min(cap, base * (2 ** (attempt - 1)))
    return delay + random.uniform(0, min(1.0, delay * 0.1))


async def with_retries(
    op: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 6,
    on_retry: Optional[RetryLogFn] = None,
    label: str = "request",
) -> T:
    """Run an async HTTP op, retrying transient failures with backoff."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await op()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            code = exc.response.status_code
            if not is_retryable_status(code) or attempt >= max_attempts:
                raise
            delay = retry_after_seconds(exc.response, attempt)
            if on_retry:
                on_retry(
                    f"{label}: HTTP {code} — retry {attempt}/{max_attempts} "
                    f"in {delay:.1f}s"
                )
            await asyncio.sleep(delay)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt >= max_attempts:
                raise
            delay = min(60.0, 2.0 * (2 ** (attempt - 1))) + random.uniform(0, 0.5)
            if on_retry:
                on_retry(
                    f"{label}: {type(exc).__name__} — retry {attempt}/{max_attempts} "
                    f"in {delay:.1f}s"
                )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
