"""migration-fleet orchestrator package."""
from .models import AgentRun, CheckResult, FleetResult, RepoTarget, Status, WaveTiming
from .cursor_client import CursorClient, MockCursorClient, RestCursorClient
from .live_status import LiveStatusBar
from .orchestrator import FleetOrchestrator, format_duration, summarize
from .report import console_table, cost_summary, render_html
from .tags import DevTag, GitHubTagPublisher, MockTagPublisher

__all__ = [
    "AgentRun",
    "CheckResult",
    "FleetResult",
    "WaveTiming",
    "RepoTarget",
    "Status",
    "CursorClient",
    "MockCursorClient",
    "RestCursorClient",
    "FleetOrchestrator",
    "LiveStatusBar",
    "DevTag",
    "MockTagPublisher",
    "GitHubTagPublisher",
    "summarize",
    "format_duration",
    "console_table",
    "cost_summary",
    "render_html",
]
