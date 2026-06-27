from contextvars import ContextVar, Token
from datetime import datetime, timezone
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.style import Style
from rich.text import Text
from typing import Dict, Optional, Callable, List, Tuple

console = Console()

# Per-scan dispatch scope (Phase 3 multi-tenant). ``progress`` is a process-wide
# singleton, so when two users run scans concurrently each registers its own SSE
# handler on the same instance. Without scoping, ``update_status`` would fan every
# agent event out to *both* handlers — one user's live scan activity (agent +
# ticker + status) leaking into the other's stream. To prevent that, an SSE route
# sets a unique scope for its scan (via :func:`set_scope`) and registers its
# handler under that scope; ``update_status`` then only calls handlers whose scope
# equals the *currently active* one. The scope rides into the scan worker thread
# because ``asyncio.create_task`` / ``asyncio.to_thread`` copy the context.
#
# Default scope is ``None`` — the CLI and any non-scoped caller register under
# ``None`` and emit under active scope ``None``, so they match each other and
# behave exactly as before (this is dormant for the local/CLI path).
_current_scope: ContextVar[Optional[str]] = ContextVar("progress_scope", default=None)


def set_scope(scope: Optional[str]) -> Token:
    """Bind the active progress dispatch scope for this context. Returns a token
    for :func:`reset_scope`. Call before launching the scan task so the scope is
    copied into the worker thread."""
    return _current_scope.set(scope)


def reset_scope(token: Token) -> None:
    """Undo a :func:`set_scope`."""
    _current_scope.reset(token)


def current_scope() -> Optional[str]:
    """The active dispatch scope for the current context."""
    return _current_scope.get()


class AgentProgress:
    """Manages progress tracking for multiple agents."""

    def __init__(self):
        self.agent_status: Dict[str, Dict[str, str]] = {}
        self.table = Table(show_header=False, box=None, padding=(0, 1))
        self.live = Live(self.table, console=console, refresh_per_second=4)
        self.started = False
        # Each entry is (scope, handler). ``scope`` is the dispatch scope the
        # handler was registered under (None for CLI/non-scoped callers).
        self.update_handlers: List[Tuple[Optional[str], Callable[..., None]]] = []

    def register_handler(self, handler: Callable[..., None], scope: Optional[str] = None):
        """Register a handler to be called when agent status updates.

        ``scope`` confines the handler to events emitted under the same scope (see
        :func:`set_scope`); the default ``None`` preserves the legacy
        broadcast-to-CLI behavior."""
        self.update_handlers.append((scope, handler))
        return handler  # Return handler to support use as decorator

    def unregister_handler(self, handler: Callable[..., None]):
        """Unregister a previously registered handler (matched by identity)."""
        self.update_handlers = [(s, h) for (s, h) in self.update_handlers if h is not handler]

    def start(self):
        """Start the progress display."""
        if not self.started:
            self.live.start()
            self.started = True

    def stop(self):
        """Stop the progress display."""
        if self.started:
            self.live.stop()
            self.started = False

    def update_status(self, agent_name: str, ticker: Optional[str] = None, status: str = "", analysis: Optional[str] = None):
        """Update the status of an agent."""
        if agent_name not in self.agent_status:
            self.agent_status[agent_name] = {"status": "", "ticker": None}

        if ticker:
            self.agent_status[agent_name]["ticker"] = ticker
        if status:
            self.agent_status[agent_name]["status"] = status
        if analysis:
            self.agent_status[agent_name]["analysis"] = analysis
        
        # Set the timestamp as UTC datetime
        timestamp = datetime.now(timezone.utc).isoformat()
        self.agent_status[agent_name]["timestamp"] = timestamp

        # Notify handlers registered under the currently active scope only, so a
        # concurrent scan for another user never receives this scan's events.
        active = _current_scope.get()
        for scope, handler in list(self.update_handlers):
            if scope == active:
                handler(agent_name, ticker, status, analysis, timestamp)

        self._refresh_display()

    def get_all_status(self):
        """Get the current status of all agents as a dictionary."""
        return {agent_name: {"ticker": info["ticker"], "status": info["status"], "display_name": self._get_display_name(agent_name)} for agent_name, info in self.agent_status.items()}

    def _get_display_name(self, agent_name: str) -> str:
        """Convert agent_name to a display-friendly format."""
        return agent_name.replace("_agent", "").replace("_", " ").title()

    def _refresh_display(self):
        """Refresh the progress display."""
        self.table.columns.clear()
        self.table.add_column(width=100)

        # Sort agents with Risk Management and Portfolio Management at the bottom
        def sort_key(item):
            agent_name = item[0]
            if "risk_management" in agent_name:
                return (2, agent_name)
            elif "portfolio_management" in agent_name:
                return (3, agent_name)
            else:
                return (1, agent_name)

        for agent_name, info in sorted(self.agent_status.items(), key=sort_key):
            status = info["status"]
            ticker = info["ticker"]
            # Create the status text with appropriate styling
            if status.lower() == "done":
                style = Style(color="green", bold=True)
                symbol = "✓"
            elif status.lower() == "error":
                style = Style(color="red", bold=True)
                symbol = "✗"
            else:
                style = Style(color="yellow")
                symbol = "⋯"

            agent_display = self._get_display_name(agent_name)
            status_text = Text()
            status_text.append(f"{symbol} ", style=style)
            status_text.append(f"{agent_display:<20}", style=Style(bold=True))

            if ticker:
                status_text.append(f"[{ticker}] ", style=Style(color="cyan"))
            status_text.append(status, style=style)

            self.table.add_row(status_text)


# Create a global instance
progress = AgentProgress()
