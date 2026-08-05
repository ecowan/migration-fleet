"""Render the fleet result as a self-contained HTML dashboard + a console table."""
from __future__ import annotations

import html
import re
from typing import Iterable, Optional

from .models import AgentRun, FleetResult, Status
from .orchestrator import format_duration, summarize
from .pricing import Rates, format_cost, price

_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_BARE_URL = re.compile(r"https?://\S+")
_MULTI_SPACE = re.compile(r"[ \t]+")


def _plain_summary(text: str, *, max_chars: int = 180) -> str:
    """Collapse agent markdown/PR bodies into one short readable blurb."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").strip()
    t = _MD_HEADING.sub("", t)
    t = _MD_BOLD.sub(r"\1", t)
    t = _MD_CODE.sub(r"\1", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _BARE_URL.sub("", t)
    # Prefer the first real paragraph; skip bare "### What changed" leftovers.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    pick = paragraphs[0] if paragraphs else t
    pick = _MULTI_SPACE.sub(" ", pick.replace("\n", " ")).strip(" :-")
    if len(pick) <= max_chars:
        return pick
    cut = pick[: max_chars - 1].rsplit(" ", 1)[0]
    return (cut or pick[: max_chars - 1]).rstrip(".,;:") + "…"

_STATUS_STYLE = {
    Status.DONE: ("#0f7b3f", "#e6f4ea", "DONE"),
    Status.NEEDS_REVIEW: ("#8a5a00", "#fdf3e0", "NEEDS REVIEW"),
    Status.BLOCKED: ("#5f4b8b", "#efeaf7", "BLOCKED"),
    Status.ERROR: ("#b3261e", "#fce8e6", "ERROR"),
    Status.RUNNING: ("#1a56db", "#e8f0fe", "RUNNING"),
    Status.PENDING: ("#5f6368", "#f1f3f4", "PENDING"),
}


def cost_summary(
    runs: list[AgentRun],
    *,
    rates: Rates,
    fleet_size: int,
) -> dict:
    """Bucket-price the run and project it to a larger fleet.

    Costs come back as a low/high interval when cache_write is unpublished;
    input / output / cache_read use Cursor's published composer-2.5 rates.
    """
    priced = [r for r in runs if r.total_tokens > 0]
    total_tokens = sum(r.total_tokens for r in priced)
    buckets = {
        "input_tokens": sum(r.input_tokens for r in priced),
        "output_tokens": sum(r.output_tokens for r in priced),
        "cache_write_tokens": sum(r.cache_write_tokens for r in priced),
        "cache_read_tokens": sum(r.cache_read_tokens for r in priced),
    }
    # A response carrying only totalTokens leaves every bucket at zero; pricing
    # that would report $0.00 for a run that cost real money. Say so instead.
    buckets_reported = sum(buckets.values()) > 0
    cost = price(rates=rates, **buckets)

    avg = (total_tokens / len(priced)) if priced else 0.0
    projected_tokens = avg * fleet_size
    scale = (fleet_size / len(priced)) if priced else 0.0
    heaviest = max(priced, key=lambda r: r.total_tokens) if priced else None
    return {
        "total_tokens": total_tokens,
        "buckets": buckets,
        "buckets_reported": buckets_reported,
        "demo_cost_low": cost["low"],
        "demo_cost_high": cost["high"],
        "exact": cost["exact"],
        "tier": cost["tier"],
        "blended_low": cost["blended_low"],
        "blended_high": cost["blended_high"],
        "avg_tokens": avg,
        "fleet_size": fleet_size,
        "projected_tokens": projected_tokens,
        "projected_cost_low": cost["low"] * scale,
        "projected_cost_high": cost["high"] * scale,
        "heaviest_name": heaviest.target.name if heaviest else None,
        "heaviest_tokens": heaviest.total_tokens if heaviest else 0,
        "priced_repos": len(priced),
    }


def console_table(
    result: FleetResult | Iterable[AgentRun],
    *,
    rates: Optional[Rates] = None,
    fleet_size: Optional[int] = None,
) -> str:
    if isinstance(result, FleetResult):
        runs = result.runs
        total_s = result.duration_s
        waves = result.waves
    else:
        runs = list(result)
        total_s = None
        waves = []

    rows = []
    for r in runs:
        _, _, label = _STATUS_STYLE[r.status]
        checks = " ".join(
            f"{'PASS' if c.passed else 'FAIL'}:{c.name}" for c in r.checks
        )
        dur = format_duration(r.duration_s)
        toks = f"{r.total_tokens:>8,}" if r.total_tokens else f"{'—':>8}"
        rows.append(
            f"  {r.target.name:<22} {label:<13} {dur:>8} {toks}  {checks}"
        )
    counts = summarize(list(runs))
    header = f"  {'REPO':<22} {'STATUS':<13} {'TIME':>8} {'TOKENS':>8}  GATES"
    footer = (
        f"  total={counts['total']}  done={counts.get('done',0)}  "
        f"needs_review={counts.get('needs_review',0)}  "
        f"blocked={counts.get('blocked',0)}  error={counts.get('error',0)}"
    )
    lines = [header, "  " + "-" * 78, *rows, "  " + "-" * 78, footer]
    if waves:
        wave_bits = ", ".join(
            f"wave {w.index + 1}={format_duration(w.duration_s)}" for w in waves
        )
        lines.append(f"  waves: {wave_bits}")
    if total_s is not None:
        lines.append(f"  elapsed: {format_duration(total_s)}")
    if rates is not None and fleet_size is not None:
        c = cost_summary(runs, rates=rates, fleet_size=fleet_size)
        if c["priced_repos"] and not c["buckets_reported"]:
            lines.append(
                f"  tokens: {c['total_tokens']:,}  (cost unavailable — API returned "
                f"no per-bucket split)"
            )
        elif c["priced_repos"]:
            b = c["buckets"]
            cost = format_cost(c["demo_cost_low"], c["demo_cost_high"], exact=c["exact"])
            blended = (
                f"${c['blended_low']:.2f}/MTok"
                if c["exact"]
                else f"${c['blended_low']:.2f}–${c['blended_high']:.2f}/MTok"
            )
            lines.append(f"  tokens: {c['total_tokens']:,}  ({cost} @ {blended}, {c['tier']})")
            lines.append(
                f"    in {b['input_tokens']:,} · out {b['output_tokens']:,} · "
                f"cache w {b['cache_write_tokens']:,} / r {b['cache_read_tokens']:,}"
            )
            if not c["exact"]:
                lines.append(
                    "    range spans unpublished cache_write — set "
                    "cache_write_per_mtok in repos.yaml to pin it"
                )
            if c["heaviest_name"]:
                lines.append(
                    f"  heaviest: {c['heaviest_name']} ({c['heaviest_tokens']:,} tokens)"
                )
            proj = format_cost(
                c["projected_cost_low"], c["projected_cost_high"], exact=c["exact"]
            )
            lines.append(
                f"  projected @ {c['fleet_size']} repos: "
                f"~{c['projected_tokens']:,.0f} tokens / {proj}"
            )
    return "\n".join(lines)


def _badge(status: Status) -> str:
    fg, bg, label = _STATUS_STYLE[status]
    return (
        f'<span class="badge" style="background:{bg};color:{fg}">{label}</span>'
    )


def _gate_chips(r: AgentRun) -> str:
    if not r.checks:
        return ""
    chips = []
    for c in r.checks:
        cls = "gate pass" if c.passed else "gate fail"
        mark = "✓" if c.passed else "✗"
        title = html.escape(c.detail or c.name, quote=True)
        chips.append(
            f'<span class="{cls}" title="{title}">{mark} {html.escape(c.name)}</span>'
        )
    return f'<div class="gates">{"".join(chips)}</div>'


def _meta_row(r: AgentRun) -> str:
    parts: list[str] = []
    if r.pr_url:
        short = r.pr_url.rsplit("/", 1)[-1]
        parts.append(
            f'<a class="meta-link" href="{html.escape(r.pr_url)}">PR #{html.escape(short)}</a>'
        )
    if r.dev_tag:
        parts.append(
            f'<span class="meta-muted"><code>{html.escape(r.dev_tag)}</code></span>'
        )
    if r.total_tokens:
        parts.append(f'<span class="meta-muted">{r.total_tokens:,} tok</span>')
    if r.duration_s is not None:
        parts.append(
            f'<span class="meta-muted">{html.escape(format_duration(r.duration_s))}</span>'
        )
    if r.artifacts:
        n = len(r.artifacts)
        names = ", ".join(str(a.get("path") or "?") for a in r.artifacts[:3])
        more = f" (+{n - 3})" if n > 3 else ""
        parts.append(
            f'<span class="meta-muted" title="{html.escape(names + more, quote=True)}">'
            f'{n} artifact{"s" if n != 1 else ""}</span>'
        )
    if not parts:
        return ""
    return f'<div class="meta">{" · ".join(parts)}</div>'


def _card(r: AgentRun) -> str:
    raw = (r.summary or r.error or "").strip()
    blurb = _plain_summary(raw) if r.status is not Status.BLOCKED else _plain_summary(raw, max_chars=220)
    if r.status is Status.BLOCKED and r.error:
        blurb = _plain_summary(r.error, max_chars=220)
    summary_html = (
        f'<p class="summary">{html.escape(blurb)}</p>' if blurb else ""
    )
    return f"""
    <article class="card">
      <header class="card-head">
        <h2 class="card-title">{html.escape(r.target.name)}</h2>
        {_badge(r.status)}
      </header>
      {summary_html}
      {_gate_chips(r)}
      {_meta_row(r)}
    </article>"""


def _waves_strip(waves: list[list[str]] | None, wave_timings=None) -> str:
    if not waves:
        return ""
    timing_by_index = {w.index: w.duration_s for w in (wave_timings or [])}
    blocks = []
    for i, names in enumerate(waves):
        chips = "".join(
            f'<span style="background:#eef1f5;border:1px solid #dfe3e8;border-radius:8px;'
            f'padding:3px 9px;font-size:12px;margin:2px">{html.escape(n)}</span>'
            for n in names
        )
        dur = timing_by_index.get(i)
        dur_html = (
            f'<span style="font-size:11px;color:#5f6368;margin-left:6px">'
            f'{html.escape(format_duration(dur))}</span>'
            if dur is not None
            else ""
        )
        blocks.append(
            f'<div style="display:flex;align-items:center;gap:8px">'
            f'<span style="font-size:11px;color:#9aa0a6;text-transform:uppercase;'
            f'letter-spacing:.05em;white-space:nowrap">wave {i+1}</span>'
            f'<div>{chips}</div>{dur_html}</div>'
        )
        if i < len(waves) - 1:
            blocks.append('<div style="color:#c0c4c9;font-size:18px">→</div>')
    return (
        '<div style="background:#fff;border:1px solid #e3e5e8;border-radius:12px;'
        'padding:14px 18px;margin-bottom:24px">'
        '<div style="font-size:12px;color:#5f6368;margin-bottom:8px">'
        'Migration order (dependency-sorted — shared libraries first):</div>'
        '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">'
        + "".join(blocks) + "</div></div>"
    )


def _status_footer(runs: list[AgentRun], total_s: Optional[float]) -> str:
    """Sticky bottom bar: segmented status bar + live time counter."""
    total = len(runs) or 1
    terminal = {
        Status.DONE, Status.NEEDS_REVIEW, Status.BLOCKED, Status.ERROR,
    }
    finished = sum(1 for r in runs if r.status in terminal)
    pct = round(100 * finished / total, 1)
    segments = []
    for status, color in (
        (Status.DONE, "#0f7b3f"),
        (Status.NEEDS_REVIEW, "#c69000"),
        (Status.BLOCKED, "#5f4b8b"),
        (Status.ERROR, "#b3261e"),
        (Status.RUNNING, "#1a56db"),
        (Status.PENDING, "#c0c4c9"),
    ):
        n = sum(1 for r in runs if r.status is status)
        if n:
            width = 100 * n / total
            segments.append(
                f'<div class="fleet-seg" data-width="{width:.2f}" '
                f'style="width:0%;background:{color};height:100%" '
                f'title="{html.escape(status.value)}: {n}"></div>'
            )
    bar = "".join(segments) or (
        '<div class="fleet-seg" data-width="0" '
        'style="width:0%;background:#1a56db;height:100%"></div>'
    )
    elapsed_s = total_s if total_s is not None else 0.0
    running = any(r.status in (Status.RUNNING, Status.PENDING) for r in runs)
    status_label = (
        "Complete" if finished >= len(runs) and runs and not running
        else f"{finished}/{len(runs)} finished"
    )
    return f"""
  <div id="fleet-status-bar" style="position:fixed;left:0;right:0;bottom:0;z-index:50;
              background:rgba(255,255,255,.96);backdrop-filter:blur(8px);
              border-top:1px solid #e3e5e8;box-shadow:0 -4px 16px rgba(0,0,0,.06)">
    <div style="height:6px;background:#eef1f5;display:flex;overflow:hidden"
         id="fleet-progress-track" data-running="{1 if running else 0}">
      {bar}
    </div>
    <div style="max-width:960px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;
                justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div style="display:flex;align-items:center;gap:12px;min-width:0">
        <div style="font-size:12px;color:#5f6368;text-transform:uppercase;letter-spacing:.05em;
                    white-space:nowrap">Fleet status</div>
        <div id="fleet-status-label" style="font-size:13px;font-weight:600;color:#202124">
          {html.escape(status_label)} · {pct:.0f}%
        </div>
      </div>
      <div style="display:flex;align-items:baseline;gap:8px;font-variant-numeric:tabular-nums">
        <span style="font-size:11px;color:#9aa0a6;text-transform:uppercase;letter-spacing:.05em">elapsed</span>
        <span id="fleet-time-counter" style="font-size:22px;font-weight:700;color:#1a56db;
              letter-spacing:-0.02em">0.0s</span>
      </div>
    </div>
  </div>
  <script>
  (function () {{
    var target = {elapsed_s:.3f};
    var el = document.getElementById("fleet-time-counter");
    var segs = document.querySelectorAll(".fleet-seg");
    function fmt(s) {{
      if (s < 60) return s.toFixed(1) + "s";
      var m = Math.floor(s / 60), rem = Math.round(s % 60);
      if (m < 60) return m + "m " + String(rem).padStart(2, "0") + "s";
      var h = Math.floor(m / 60); m = m % 60;
      return h + "h " + String(m).padStart(2, "0") + "m " + String(rem).padStart(2, "0") + "s";
    }}
    // Expand status segments after first paint.
    requestAnimationFrame(function () {{
      segs.forEach(function (seg) {{
        seg.style.width = seg.getAttribute("data-width") + "%";
      }});
    }});
    if (!el) return;
    var start = performance.now();
    var duration = Math.min(1200, Math.max(400, target * 80));
    function tick(now) {{
      var t = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = fmt(target * eased);
      if (t < 1) requestAnimationFrame(tick);
      else el.textContent = fmt(target);
    }}
    requestAnimationFrame(tick);
  }})();
  </script>"""


def _cost_banner(
    runs: list[AgentRun],
    *,
    rates: Optional[Rates],
    fleet_size: Optional[int],
) -> str:
    if rates is None or fleet_size is None:
        return ""
    c = cost_summary(runs, rates=rates, fleet_size=fleet_size)
    if not c["priced_repos"] or not c["buckets_reported"]:
        return ""
    demo = format_cost(c["demo_cost_low"], c["demo_cost_high"], exact=c["exact"])
    proj = format_cost(
        c["projected_cost_low"], c["projected_cost_high"], exact=c["exact"]
    )
    b = c["buckets"]
    mix = (
        f'<div style="font-size:12px;color:#5f6368;margin-top:10px">'
        f'in {b["input_tokens"]:,} · out {b["output_tokens"]:,} · '
        f'cache write {b["cache_write_tokens"]:,} · cache read {b["cache_read_tokens"]:,}'
        f'</div>'
    )
    caveat = "" if c["exact"] else (
        f'<div style="font-size:12px;color:#8a5a00;background:#fdf3e0;border-radius:8px;'
        f'padding:8px 10px;margin-top:10px">'
        f'Range spans unpublished <code>cache_write</code> (0&ndash;1.25&times; input). '
        f'Cache read uses the published composer-2.5 rate. Set '
        f'<code>cache_write_per_mtok</code> in repos.yaml to pin the total.'
        f'</div>'
    )
    heavy = ""
    if c["heaviest_name"]:
        heavy = (
            f'<div style="font-size:13px;color:#5f6368;margin-top:8px">'
            f'Spend concentrates in <strong style="color:#202124">'
            f'{html.escape(c["heaviest_name"])}</strong> '
            f'({c["heaviest_tokens"]:,} tokens) — the gnarly repo drives cost.'
            f'</div>'
        )
    return f"""
    <div style="background:linear-gradient(135deg,#f0f4ff 0%,#fff 60%);border:1px solid #d7e0f5;
                border-radius:12px;padding:18px 20px;margin-bottom:24px">
      <div style="font-size:12px;color:#5f6368;text-transform:uppercase;letter-spacing:.05em;
                  margin-bottom:10px">Cost · GET /v1/agents/{{id}}/usage</div>
      <div style="display:flex;gap:28px;flex-wrap:wrap;align-items:baseline">
        <div>
          <div style="font-size:28px;font-weight:700;color:#1a56db;letter-spacing:-0.02em">
            {c["total_tokens"]:,}</div>
          <div style="font-size:12px;color:#5f6368">tokens this run
            · {demo}</div>
        </div>
        <div style="color:#c0c4c9;font-size:22px">→</div>
        <div>
          <div style="font-size:28px;font-weight:700;color:#202124;letter-spacing:-0.02em">
            {proj}</div>
          <div style="font-size:12px;color:#5f6368">projected @ {c["fleet_size"]} repos
            · {c["tier"]} tier</div>
        </div>
      </div>
      {mix}
      {caveat}
      {heavy}
    </div>"""


def render_html(
    result: FleetResult | list[AgentRun],
    *,
    title: str,
    subtitle: str,
    waves: list[list[str]] | None = None,
    rates: Optional[Rates] = None,
    fleet_size: Optional[int] = None,
) -> str:
    if isinstance(result, FleetResult):
        runs = result.runs
        total_s: Optional[float] = result.duration_s
        wave_timings = result.waves
    else:
        runs = result
        total_s = None
        wave_timings = []

    counts = summarize(runs)
    cards = "\n".join(_card(r) for r in runs)
    elapsed = format_duration(total_s) if total_s is not None else "—"
    footer = _status_footer(runs, total_s)
    cost = _cost_banner(runs, rates=rates, fleet_size=fleet_size)
    stat = lambda n, k, c: (  # noqa: E731
        f'<div style="text-align:center"><div style="font-size:30px;font-weight:700;color:{c}">'
        f'{counts.get(k,0)}</div><div style="font-size:12px;color:#5f6368;text-transform:uppercase;'
        f'letter-spacing:.05em">{n}</div></div>'
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{
    --ink: #202124;
    --muted: #5f6368;
    --faint: #9aa0a6;
    --line: #e3e5e8;
    --bg: #f6f7f9;
    --card: #fff;
    --link: #1a56db;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    line-height: 1.45;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 32px 24px 96px; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; letter-spacing: -0.02em; }}
  .subtitle {{ color: var(--muted); font-size: 14px; margin-bottom: 24px; }}
  .stats {{
    display: flex; gap: 24px; justify-content: space-around; flex-wrap: wrap;
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 20px; margin-bottom: 24px;
  }}
  .cards {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
  }}
  @media (max-width: 720px) {{
    .cards {{ grid-template-columns: 1fr; }}
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 18px 20px;
    box-shadow: 0 1px 2px rgba(0,0,0,.04);
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-height: 0;
  }}
  .card-head {{
    display: flex; justify-content: space-between; align-items: center; gap: 12px;
  }}
  .card-title {{
    margin: 0; font-size: 16px; font-weight: 650; letter-spacing: -0.01em;
  }}
  .badge {{
    padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 650;
    letter-spacing: .02em; white-space: nowrap; flex-shrink: 0;
  }}
  .summary {{
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.5;
  }}
  .gates {{
    display: flex; flex-wrap: wrap; gap: 6px;
  }}
  .gate {{
    font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 6px;
    white-space: nowrap; letter-spacing: .01em;
  }}
  .gate.pass {{ background: #e6f4ea; color: #0f7b3f; }}
  .gate.fail {{ background: #fce8e6; color: #b3261e; }}
  .meta {{
    margin-top: auto;
    padding-top: 10px;
    border-top: 1px solid #eef1f5;
    font-size: 12px;
    color: var(--muted);
    display: flex; flex-wrap: wrap; gap: 4px 0;
    align-items: baseline;
  }}
  .meta-link {{ color: var(--link); text-decoration: none; font-weight: 600; }}
  .meta-link:hover {{ text-decoration: underline; }}
  .meta-muted {{ color: var(--muted); }}
  .meta code {{
    font-size: 11px; background: #eef1f5; padding: 1px 5px; border-radius: 4px;
  }}
  .foot {{
    color: var(--faint); font-size: 12px; margin-top: 24px; text-align: center;
  }}
  #fleet-progress-track > div {{
    transition: width 0.8s cubic-bezier(.22,1,.36,1);
  }}
  @keyframes fleet-bar-pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.7; }}
  }}
  #fleet-progress-track[data-running="1"] > div:last-child {{
    animation: fleet-bar-pulse 1.2s ease-in-out infinite;
  }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>{html.escape(title)}</h1>
    <div class="subtitle">{html.escape(subtitle)}</div>
    {_waves_strip(waves, wave_timings)}
    {cost}
    <div class="stats">
      {stat("repos", "total", "#202124")}
      {stat("done", "done", "#0f7b3f")}
      {stat("needs review", "needs_review", "#8a5a00")}
      {stat("blocked", "blocked", "#5f4b8b")}
      {stat("error", "error", "#b3261e")}
      <div style="text-align:center"><div style="font-size:30px;font-weight:700;color:#1a56db">
        {html.escape(elapsed)}</div><div style="font-size:12px;color:#5f6368;text-transform:uppercase;
        letter-spacing:.05em">elapsed</div></div>
    </div>
    <div class="cards">{cards}</div>
    <div class="foot">
      Generated by migration-fleet · human review required before merge
    </div>
  </div>
  {footer}
</body></html>"""
