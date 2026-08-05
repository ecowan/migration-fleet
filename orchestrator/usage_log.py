"""Persist per-agent token usage from GET /v1/agents/{id}/usage.

The orchestrator fetches usage after each agent reaches a terminal state. This
module writes a JSON sidecar next to the HTML dashboard containing:

  * every bucket, per repo, plus fleet totals (from the API response)
  * `usage_raw` — the API body verbatim
  * optional `spend` — bucket × rates from pricing.py when Rates are supplied

Pricing is optional so the measurement can be re-rated later without re-running
the fleet.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import AgentRun, FleetResult
from .pricing import Rates, price_receipt

SCHEMA_VERSION = 2

_BUCKETS = ("inputTokens", "outputTokens", "cacheWriteTokens", "cacheReadTokens")


def _run_record(run: AgentRun) -> dict:
    return {
        "repo": run.target.name,
        "agent_id": run.agent_id,
        "status": run.status.value,
        "duration_s": run.duration_s,
        "pr_url": run.pr_url,
        "tokens": {
            "inputTokens": run.input_tokens,
            "outputTokens": run.output_tokens,
            "cacheWriteTokens": run.cache_write_tokens,
            "cacheReadTokens": run.cache_read_tokens,
            "totalTokens": run.total_tokens,
        },
        # Verbatim API body (or mock equivalent).
        "usage_raw": run.usage,
    }


def build_usage_log(
    result: FleetResult | list[AgentRun],
    *,
    mode: str,
    model: Optional[str] = None,
    fleet_size: Optional[int] = None,
    rates: Optional[Rates] = None,
) -> dict:
    """Assemble the usage record for a completed fleet run."""
    runs = result.runs if isinstance(result, FleetResult) else list(result)
    records = [_run_record(r) for r in runs]
    priced = [r for r in runs if r.total_tokens > 0]

    totals = {k: 0 for k in _BUCKETS}
    for r in priced:
        totals["inputTokens"] += r.input_tokens
        totals["outputTokens"] += r.output_tokens
        totals["cacheWriteTokens"] += r.cache_write_tokens
        totals["cacheReadTokens"] += r.cache_read_tokens
    totals["totalTokens"] = sum(r.total_tokens for r in priced)

    grand = totals["totalTokens"]
    shares = (
        {k: round(totals[k] / grand, 6) for k in _BUCKETS} if grand else
        {k: 0.0 for k in _BUCKETS}
    )
    bucket_sum = sum(totals[k] for k in _BUCKETS)

    record: dict = {
        "schema": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "model": model,
        "fleet_size": fleet_size,
        "duration_s": result.duration_s if isinstance(result, FleetResult) else None,
        "repos_total": len(runs),
        "repos_priced": len(priced),
        "totals": totals,
        "bucket_share": shares,
        "buckets_reported": bucket_sum > 0,
        "runs": records,
        "source": "GET /v1/agents/{id}/usage",
    }

    if rates is not None and bucket_sum > 0:
        receipt = price_receipt(
            input_tokens=totals["inputTokens"],
            output_tokens=totals["outputTokens"],
            cache_write_tokens=totals["cacheWriteTokens"],
            cache_read_tokens=totals["cacheReadTokens"],
            rates=rates,
        )
        record["spend"] = {
            "tier": receipt["tier"],
            "exact": receipt["exact"],
            "spend_low": receipt["spend_low"],
            "spend_high": receipt["spend_high"],
            "lines": receipt["lines"],
        }
        record["receipt"] = receipt

    return record


def write_usage_log(
    result: FleetResult | list[AgentRun],
    path: Path,
    *,
    mode: str,
    model: Optional[str] = None,
    fleet_size: Optional[int] = None,
    rates: Optional[Rates] = None,
) -> dict:
    """Write the usage log to `path` and return the record that was written."""
    record = build_usage_log(
        result,
        mode=mode,
        model=model,
        fleet_size=fleet_size,
        rates=rates,
    )
    path.write_text(json.dumps(record, indent=2) + "\n")
    return record
