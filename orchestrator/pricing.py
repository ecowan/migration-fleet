"""Bucket-level pricing over Cloud Agent usage from GET /v1/agents/{id}/usage.

Flow:
  1. RestCursorClient.usage() fetches the API body (totalUsage + per-run rows).
  2. AgentRun exposes input/output/cacheWrite/cacheRead token counts.
  3. This module multiplies each bucket by its $/MTok rate and sums spend.

Composer 2.5 published rates (Cursor model docs / Teams pricing table):

    standard   $0.50 in · $0.20 cache read · $2.50 out
    fast       $3.00 in · $0.50 cache read · $15.00 out   (product default)

Cache *write* is still unpublished for composer-2.5 (table shows "-"). When the
rate is unknown we price it as an interval (0x .. 1.25x input) so the receipt
does not invent a point estimate. Set `cache_write_per_mtok` in repos.yaml to
collapse the range.

No Cursor Token Rate — first-party models are exempt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Published composer-2.5 rates, per million tokens.
TIERS: dict[str, dict[str, float]] = {
    "standard": {
        "input": 0.50,
        "output": 2.50,
        "cache_read": 0.20,
    },
    "fast": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.50,
    },
}
DEFAULT_TIER = "fast"  # "Fast is the default in the product" — Cursor model docs

# Bounds for unpublished cache-write rate, as multiples of the input rate.
CACHE_WRITE_BOUNDS = (0.0, 1.25)


@dataclass
class Rates:
    """Per-million-token rates. `cache_write_per_mtok is None` → unpublished."""
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: Optional[float] = None
    tier: str = DEFAULT_TIER
    cache_write_bounds: tuple[float, float] = field(default=CACHE_WRITE_BOUNDS)

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> Optional["Rates"]:
        """Build from the `pricing:` block in repos.yaml. None if absent."""
        if not cfg:
            return None
        tier = str(cfg.get("tier") or DEFAULT_TIER).lower()
        if tier not in TIERS:
            raise ValueError(
                f"unknown pricing tier {tier!r}; expected one of {sorted(TIERS)}"
            )
        pub = TIERS[tier]
        in_rate = _opt_float(cfg.get("input_per_mtok"))
        out_rate = _opt_float(cfg.get("output_per_mtok"))
        cr_rate = _opt_float(cfg.get("cache_read_per_mtok"))
        cw_rate = _opt_float(cfg.get("cache_write_per_mtok"))
        in_rate = pub["input"] if in_rate is None else in_rate
        out_rate = pub["output"] if out_rate is None else out_rate
        cr_rate = pub["cache_read"] if cr_rate is None else cr_rate
        # Explicit overrides that disagree with the tier label get a custom tag.
        label = tier
        if (in_rate, out_rate, cr_rate) != (
            pub["input"], pub["output"], pub["cache_read"]
        ):
            label = (
                f"custom (${in_rate:g}/${cr_rate:g}/${out_rate:g}, "
                f"{tier} lists ${pub['input']:g}/${pub['cache_read']:g}/${pub['output']:g})"
            )
        return cls(
            input_per_mtok=in_rate,
            output_per_mtok=out_rate,
            cache_read_per_mtok=cr_rate,
            cache_write_per_mtok=cw_rate,
            tier=label,
        )

    @property
    def cache_write_known(self) -> bool:
        return self.cache_write_per_mtok is not None

    def cache_write_range(self) -> tuple[float, float]:
        if self.cache_write_per_mtok is not None:
            return (self.cache_write_per_mtok, self.cache_write_per_mtok)
        lo, hi = self.cache_write_bounds
        return (self.input_per_mtok * lo, self.input_per_mtok * hi)


def _opt_float(v) -> Optional[float]:
    return None if v is None or v == "" else float(v)


def _mtok(tokens: int, rate: float) -> float:
    return tokens * rate / 1_000_000


def price(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int,
    cache_read_tokens: int,
    rates: Rates,
) -> dict:
    """Cost for one bucket set. Returns a low/high interval plus its width.

    Interval is degenerate (exact) when cache-write rate is known.
    """
    receipt = price_receipt(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_tokens=cache_read_tokens,
        rates=rates,
    )
    total_tokens = receipt["total_tokens"]
    low, high = receipt["spend_low"], receipt["spend_high"]
    return {
        "low": low,
        "high": high,
        "exact": receipt["exact"],
        "fixed_component": receipt["fixed_component"],
        "tier": rates.tier,
        "blended_low": (low / total_tokens * 1_000_000) if total_tokens else 0.0,
        "blended_high": (high / total_tokens * 1_000_000) if total_tokens else 0.0,
        "lines": receipt["lines"],
        "total_tokens": total_tokens,
    }


def price_receipt(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int,
    cache_read_tokens: int,
    rates: Rates,
) -> dict:
    """Per-bucket tokens × rate → spend, ready to print or serialize."""
    cw_lo, cw_hi = rates.cache_write_range()
    lines = [
        {
            "bucket": "input",
            "tokens": input_tokens,
            "rate_low": rates.input_per_mtok,
            "rate_high": rates.input_per_mtok,
            "spend_low": _mtok(input_tokens, rates.input_per_mtok),
            "spend_high": _mtok(input_tokens, rates.input_per_mtok),
            "known": True,
        },
        {
            "bucket": "output",
            "tokens": output_tokens,
            "rate_low": rates.output_per_mtok,
            "rate_high": rates.output_per_mtok,
            "spend_low": _mtok(output_tokens, rates.output_per_mtok),
            "spend_high": _mtok(output_tokens, rates.output_per_mtok),
            "known": True,
        },
        {
            "bucket": "cache_write",
            "tokens": cache_write_tokens,
            "rate_low": cw_lo,
            "rate_high": cw_hi,
            "spend_low": _mtok(cache_write_tokens, cw_lo),
            "spend_high": _mtok(cache_write_tokens, cw_hi),
            "known": rates.cache_write_known,
        },
        {
            "bucket": "cache_read",
            "tokens": cache_read_tokens,
            "rate_low": rates.cache_read_per_mtok,
            "rate_high": rates.cache_read_per_mtok,
            "spend_low": _mtok(cache_read_tokens, rates.cache_read_per_mtok),
            "spend_high": _mtok(cache_read_tokens, rates.cache_read_per_mtok),
            "known": True,
        },
    ]
    spend_low = sum(line["spend_low"] for line in lines)
    spend_high = sum(line["spend_high"] for line in lines)
    fixed = sum(line["spend_low"] for line in lines if line["known"])
    total_tokens = (
        input_tokens + output_tokens + cache_write_tokens + cache_read_tokens
    )
    return {
        "lines": lines,
        "total_tokens": total_tokens,
        "spend_low": spend_low,
        "spend_high": spend_high,
        "exact": rates.cache_write_known,
        "fixed_component": fixed,
        "tier": rates.tier,
    }


def format_cost(low: float, high: float, *, exact: bool, places: int = 2) -> str:
    """'$2.07' when pinned down, '$0.92 – $2.07' when cache-write rate is unknown."""
    if exact or abs(high - low) < 10 ** -(places + 1):
        return f"${low:,.{places}f}"
    return f"${low:,.{places}f} – ${high:,.{places}f}"


def format_rate(low: float, high: float, *, known: bool) -> str:
    if known or abs(high - low) < 1e-9:
        return f"${low:g}"
    return f"${low:g}–${high:g}"


def format_receipt(receipt: dict, *, places: int = 2) -> str:
    """Human-readable end-of-run usage + spend table."""
    lines_out = [
        f"── Usage & spend ({receipt['tier']}) ──",
        f"  {'bucket':<12} {'tokens':>12} {'$/MTok':>14} {'spend':>16}",
        "  " + "-" * 58,
    ]
    for line in receipt["lines"]:
        rate = format_rate(line["rate_low"], line["rate_high"], known=line["known"])
        spend = format_cost(
            line["spend_low"], line["spend_high"], exact=line["known"], places=places
        )
        note = "" if line["known"] else " *"
        lines_out.append(
            f"  {line['bucket']:<12} {line['tokens']:>12,} {rate:>14} {spend:>16}{note}"
        )
    lines_out.append("  " + "-" * 58)
    total = format_cost(
        receipt["spend_low"],
        receipt["spend_high"],
        exact=receipt["exact"],
        places=places,
    )
    lines_out.append(
        f"  {'total':<12} {receipt['total_tokens']:>12,} {'':>14} {total:>16}"
    )
    if not receipt["exact"]:
        lines_out.append(
            "  * cache_write rate unpublished — range is 0×..1.25× input; "
            "set cache_write_per_mtok in repos.yaml to pin it"
        )
    return "\n".join(lines_out)
