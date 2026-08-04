"""Render the fleet result as a self-contained HTML dashboard + a console table."""
from __future__ import annotations

import html
from typing import Iterable, Optional

from .models import AgentRun, FleetResult, Status
from .orchestrator import format_duration, summarize

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
    cost_per_mtok: float,
    fleet_size: int,
) -> dict:
    """Aggregate token usage and project cost to a larger fleet size."""
    priced = [r for r in runs if r.total_tokens > 0]
    total_tokens = sum(r.total_tokens for r in priced)
    demo_cost = total_tokens / 1_000_000 * cost_per_mtok
    avg = (total_tokens / len(priced)) if priced else 0.0
    projected_tokens = avg * fleet_size
    projected_cost = projected_tokens / 1_000_000 * cost_per_mtok
    heaviest = max(priced, key=lambda r: r.total_tokens) if priced else None
    return {
        "total_tokens": total_tokens,
        "demo_cost": demo_cost,
        "avg_tokens": avg,
        "fleet_size": fleet_size,
        "projected_tokens": projected_tokens,
        "projected_cost": projected_cost,
        "cost_per_mtok": cost_per_mtok,
        "heaviest_name": heaviest.target.name if heaviest else None,
        "heaviest_tokens": heaviest.total_tokens if heaviest else 0,
        "priced_repos": len(priced),
    }


def console_table(
    result: FleetResult | Iterable[AgentRun],
    *,
    cost_per_mtok: Optional[float] = None,
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
    if cost_per_mtok is not None and fleet_size is not None:
        c = cost_summary(runs, cost_per_mtok=cost_per_mtok, fleet_size=fleet_size)
        if c["priced_repos"]:
            lines.append(
                f"  tokens: {c['total_tokens']:,}  (~${c['demo_cost']:.2f} @ "
                f"${c['cost_per_mtok']:.2f}/MTok)"
            )
            if c["heaviest_name"]:
                lines.append(
                    f"  heaviest: {c['heaviest_name']} ({c['heaviest_tokens']:,} tokens)"
                )
            lines.append(
                f"  projected @ {c['fleet_size']} repos: "
                f"~{c['projected_tokens']:,.0f} tokens / ${c['projected_cost']:.2f}"
            )
    return "\n".join(lines)


def _badge(status: Status) -> str:
    fg, bg, label = _STATUS_STYLE[status]
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 10px;border-radius:12px;'
        f'font-size:12px;font-weight:600;white-space:nowrap">{label}</span>'
    )


def _card(r: AgentRun) -> str:
    pr = (
        f'<a href="{html.escape(r.pr_url)}" style="color:#1a56db;text-decoration:none">'
        f"{html.escape(r.pr_url.rsplit('/',2)[-2]+'/'+r.pr_url.rsplit('/',1)[-1])}</a>"
        if r.pr_url
        else '<span style="color:#9aa0a6">—</span>'
    )
    checks = "".join(
        f'<li style="margin:2px 0;color:{"#0f7b3f" if c.passed else "#b3261e"}">'
        f'{"✓" if c.passed else "✗"} {html.escape(c.name)}'
        f'<span style="color:#9aa0a6;font-weight:400"> — {html.escape(c.detail)}</span></li>'
        for c in r.checks
    )
    detail = html.escape(r.summary or r.error or "")
    dur = format_duration(r.duration_s)
    tag = (
        f'<div style="font-size:12px;color:#1a56db;margin:0 0 10px">'
        f'cursor.dev · <code>{html.escape(r.dev_tag)}</code></div>'
        if r.dev_tag
        else ""
    )
    toks = (
        f'<span style="color:#5f6368;white-space:nowrap">'
        f'{r.total_tokens:,} tok</span>'
        if r.total_tokens
        else ""
    )
    arts = ""
    if r.artifacts:
        items = "".join(
            f'<li style="margin:2px 0;color:#3c4043">'
            f'<code style="font-size:12px">{html.escape(str(a.get("path") or "?"))}</code>'
            f'</li>'
            for a in r.artifacts[:5]
        )
        arts = (
            f'<div style="font-size:12px;color:#5f6368;margin:0 0 4px">artifacts</div>'
            f'<ul style="list-style:none;padding:0;margin:0 0 12px;font-size:12px">{items}</ul>'
        )
    return f"""
    <div style="border:1px solid #e3e5e8;border-radius:12px;padding:18px 20px;background:#fff;
                box-shadow:0 1px 2px rgba(0,0,0,.04)">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
        <div style="font-weight:650;font-size:16px">{html.escape(r.target.name)}</div>
        {_badge(r.status)}
      </div>
      <div style="color:#5f6368;font-size:13px;margin:6px 0 12px">{detail}</div>
      {tag}
      <ul style="list-style:none;padding:0;margin:0 0 12px;font-size:13px">{checks}</ul>
      {arts}
      <div style="display:flex;justify-content:space-between;gap:12px;font-size:13px;color:#3c4043">
        <div>PR: {pr}</div>
        <div style="display:flex;gap:12px;color:#5f6368;white-space:nowrap">
          {toks}
          <span>⏱ {html.escape(dur)}</span>
        </div>
      </div>
    </div>"""


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
    cost_per_mtok: Optional[float],
    fleet_size: Optional[int],
) -> str:
    if cost_per_mtok is None or fleet_size is None:
        return ""
    c = cost_summary(runs, cost_per_mtok=cost_per_mtok, fleet_size=fleet_size)
    if not c["priced_repos"]:
        return ""
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
            · ~${c["demo_cost"]:.2f}</div>
        </div>
        <div style="color:#c0c4c9;font-size:22px">→</div>
        <div>
          <div style="font-size:28px;font-weight:700;color:#202124;letter-spacing:-0.02em">
            ${c["projected_cost"]:.2f}</div>
          <div style="font-size:12px;color:#5f6368">projected @ {c["fleet_size"]} repos
            · ${c["cost_per_mtok"]:.2f}/MTok</div>
        </div>
      </div>
      {heavy}
    </div>"""


def render_html(
    result: FleetResult | list[AgentRun],
    *,
    title: str,
    subtitle: str,
    waves: list[list[str]] | None = None,
    cost_per_mtok: Optional[float] = None,
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
    cost = _cost_banner(runs, cost_per_mtok=cost_per_mtok, fleet_size=fleet_size)
    stat = lambda n, k, c: (  # noqa: E731
        f'<div style="text-align:center"><div style="font-size:30px;font-weight:700;color:{c}">'
        f'{counts.get(k,0)}</div><div style="font-size:12px;color:#5f6368;text-transform:uppercase;'
        f'letter-spacing:.05em">{n}</div></div>'
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
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
<body style="margin:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#202124">
  <div style="max-width:960px;margin:0 auto;padding:32px 24px 96px">
    <h1 style="font-size:24px;margin:0 0 4px">{html.escape(title)}</h1>
    <div style="color:#5f6368;font-size:14px;margin-bottom:24px">{html.escape(subtitle)}</div>
    {_waves_strip(waves, wave_timings)}
    {cost}
    <div style="display:flex;gap:32px;justify-content:space-around;background:#fff;border:1px solid #e3e5e8;
                border-radius:12px;padding:20px;margin-bottom:24px">
      {stat("repos", "total", "#202124")}
      {stat("done", "done", "#0f7b3f")}
      {stat("needs review", "needs_review", "#8a5a00")}
      {stat("blocked", "blocked", "#5f4b8b")}
      {stat("error", "error", "#b3261e")}
      <div style="text-align:center"><div style="font-size:30px;font-weight:700;color:#1a56db">
        {html.escape(elapsed)}</div><div style="font-size:12px;color:#5f6368;text-transform:uppercase;
        letter-spacing:.05em">elapsed</div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">{cards}</div>
    <div style="color:#9aa0a6;font-size:12px;margin-top:24px;text-align:center">
      Generated by migration-fleet · human review required before merge
    </div>
  </div>
  {footer}
</body></html>"""
