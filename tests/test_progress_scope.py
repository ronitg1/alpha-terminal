"""Tests for scope-aware progress dispatch (Phase 3, step 2).

``progress`` is a process-wide singleton; two concurrent scans register their own
SSE handlers on it. Dispatch must deliver each agent event ONLY to the handler
registered under the currently-active scope, so one user's live scan activity
never leaks into another user's stream. The default ``None`` scope preserves the
legacy CLI broadcast behavior.
"""
from __future__ import annotations

from src.utils import progress as progress_mod


def test_scoped_dispatch_only_hits_matching_scope():
    p = progress_mod.AgentProgress()
    a_events: list = []
    b_events: list = []
    p.register_handler(lambda *args: a_events.append(args), scope="scan-A")
    p.register_handler(lambda *args: b_events.append(args), scope="scan-B")

    token = progress_mod.set_scope("scan-A")
    try:
        p.update_status("alpha_seeker", "NVDA", "done")
    finally:
        progress_mod.reset_scope(token)

    assert len(a_events) == 1
    assert a_events[0][0] == "alpha_seeker"
    assert b_events == []  # B's handler must NOT have seen A's event


def test_unscoped_handler_matches_default_scope():
    # CLI / legacy path: no scope set on either side -> handler fires (back-compat).
    p = progress_mod.AgentProgress()
    cli_events: list = []
    p.register_handler(lambda *args: cli_events.append(args))  # scope=None

    p.update_status("fundamentals", "AAPL", "done")  # active scope is None (default)

    assert len(cli_events) == 1


def test_scoped_handler_silent_when_no_scope_active():
    # A scoped handler must not fire for an unscoped (None) emission.
    p = progress_mod.AgentProgress()
    seen: list = []
    p.register_handler(lambda *args: seen.append(args), scope="scan-A")

    p.update_status("technicals", "MSFT", "done")  # active scope None != "scan-A"

    assert seen == []


def test_unregister_removes_only_target_handler():
    p = progress_mod.AgentProgress()

    def h1(*args):
        pass

    def h2(*args):
        pass

    p.register_handler(h1, scope="s")
    p.register_handler(h2, scope="s")
    p.unregister_handler(h1)

    remaining = [h for (_scope, h) in p.update_handlers]
    assert remaining == [h2]
