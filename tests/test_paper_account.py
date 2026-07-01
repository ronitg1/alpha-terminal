"""Paper-trading simulated-account math (cash derived from positions)."""
from __future__ import annotations

from app.backend.services import pnl_service


def _opt(id_, entry, status, qty=1.0, exit_=None):
    return {
        "id": id_, "kind": "option", "ticker": "NVDA", "side": "long", "qty": qty,
        "entry_price": entry, "status": status, "exit_price": exit_,
        "option": {"type": "call", "strike": 100.0, "expiration": "2026-12-18"},
    }


def test_account_snapshot_cash_and_pnl():
    positions = [
        _opt("a", 5.0, "open"),                 # open long: -500 cash, marked at 6.00
        _opt("b", 5.0, "closed", exit_=8.0),    # closed: -500 then +800 => +300 realized
    ]
    marks = {"a": 6.0}
    snap = pnl_service.account_snapshot(positions, marks)

    # cash = 100000 - 500 (open a) - 500 (open b) + 800 (close b) = 99800
    assert snap["cash"] == 99800.0
    assert snap["buying_power"] == 99800.0
    # equity = cash + market value of open (6*100) = 100400
    assert snap["positions_value"] == 600.0
    assert snap["equity"] == 100400.0
    assert snap["realized"] == 300.0
    assert snap["unrealized"] == 100.0
    # total P&L equals realized + unrealized by construction
    assert snap["total_pnl"] == 400.0
    assert snap["starting_cash"] == 100000.0


def test_account_snapshot_empty_is_full_cash():
    snap = pnl_service.account_snapshot([], {})
    assert snap["cash"] == 100000.0
    assert snap["equity"] == 100000.0
    assert snap["total_pnl"] == 0.0
