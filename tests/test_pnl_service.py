"""P&L service: CRUD, money math, and summary aggregation.

Uses a temp store path so tests never touch real user data in app/data/.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.backend.services import pnl_service


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the service at a throwaway JSON file for every test."""
    monkeypatch.setattr(pnl_service, "_DATA_PATH", tmp_path / "pnl_positions.json")
    yield


def _make_option(**overrides):
    base = {
        "kind": "option",
        "ticker": "NVDA",
        "side": "long",
        "qty": 2,
        "option": {"type": "call", "strike": 200.0, "expiration": "2026-07-17", "contract_ticker": None},
        "entry_price": 5.40,
        "entry_date": "2026-06-01",
        "source": "manual",
        "real": False,
    }
    base.update(overrides)
    return base


def test_create_and_list_roundtrip():
    rec = pnl_service.create(_make_option())
    assert rec["id"].startswith("pos_")
    assert rec["ticker"] == "NVDA"
    stored = pnl_service.get_all()
    assert len(stored) == 1
    assert stored[0]["id"] == rec["id"]


def test_create_rejects_bad_fields():
    with pytest.raises(ValueError):
        pnl_service.create(_make_option(qty=0))
    with pytest.raises(ValueError):
        pnl_service.create(_make_option(kind="option", option=None))
    with pytest.raises(ValueError):
        pnl_service.create(_make_option(side="sideways"))


def test_option_long_realized_pnl_uses_100x_multiplier():
    rec = pnl_service.create(_make_option())
    pnl_service.close(rec["id"], exit_price=8.10, exit_date="2026-06-09")
    (closed,) = pnl_service.get_all()
    # (8.10 - 5.40) * 2 contracts * 100 = +540
    assert pnl_service.realized_pnl(closed) == pytest.approx(540.0)


def test_short_option_pnl_inverts():
    rec = pnl_service.create(_make_option(side="short", entry_price=3.00))
    pnl_service.close(rec["id"], exit_price=1.00)
    (closed,) = pnl_service.get_all()
    # Sold at 3.00, bought back at 1.00 → +2.00/share * 2 * 100 = +400
    assert pnl_service.realized_pnl(closed) == pytest.approx(400.0)


def test_stock_pnl_uses_1x_multiplier():
    rec = pnl_service.create(
        {"kind": "stock", "ticker": "AAPL", "qty": 10, "entry_price": 150.0, "side": "long"}
    )
    pnl_service.close(rec["id"], exit_price=155.0)
    (closed,) = pnl_service.get_all()
    assert pnl_service.realized_pnl(closed) == pytest.approx(50.0)


def test_unrealized_pnl_against_mark():
    rec = pnl_service.create(_make_option())
    assert pnl_service.unrealized_pnl(rec, mark=6.40) == pytest.approx(200.0)
    assert pnl_service.unrealized_pnl(rec, mark=None) is None
    pnl_service.close(rec["id"], exit_price=6.40)
    (closed,) = pnl_service.get_all()
    assert pnl_service.unrealized_pnl(closed, mark=9.99) is None  # closed → no unrealized


def test_update_and_delete():
    rec = pnl_service.create(_make_option())
    updated = pnl_service.update(rec["id"], {"notes": "thesis: DC capex", "qty": 3})
    assert updated["notes"] == "thesis: DC capex"
    assert updated["qty"] == 3
    assert pnl_service.update("pos_nope", {"notes": "x"}) is None
    assert pnl_service.delete(rec["id"]) is True
    assert pnl_service.delete(rec["id"]) is False
    assert pnl_service.get_all() == []


def test_summary_math():
    win = pnl_service.create(_make_option())
    pnl_service.close(win["id"], exit_price=8.10, exit_date="2026-06-05")  # +540
    loss = pnl_service.create(_make_option(entry_price=4.00, qty=1))
    pnl_service.close(loss["id"], exit_price=3.00, exit_date="2026-06-08")  # -100
    open_pos = pnl_service.create(_make_option(ticker="MSFT", entry_price=2.00, qty=1))

    positions = pnl_service.get_all()
    s = pnl_service.summarize(positions, {open_pos["id"]: 2.50})

    assert s["n_open"] == 1
    assert s["n_closed"] == 2
    assert s["realized_total"] == pytest.approx(440.0)
    assert s["unrealized_total"] == pytest.approx(50.0)
    assert s["n_wins"] == 1 and s["n_losses"] == 1
    assert s["win_rate"] == 50.0
    assert s["by_underlying"]["NVDA"]["realized"] == pytest.approx(440.0)
    assert s["by_underlying"]["MSFT"]["unrealized"] == pytest.approx(50.0)
    # Equity curve is chronological + cumulative.
    assert [pt["cum_realized"] for pt in s["equity_curve"]] == [540.0, 440.0]


def test_realized_sharpe_gates_thin_history():
    # 4 trade dates (< 5) over a wide span → None; snapshot mirrors it.
    for i, day in enumerate(["2026-01-05", "2026-01-20", "2026-02-03", "2026-02-17"]):
        rec = pnl_service.create(_make_option())
        pnl_service.close(rec["id"], exit_price=6.00 + i, exit_date=day)
    positions = pnl_service.get_all()
    assert pnl_service.realized_sharpe(positions) is None
    snap = pnl_service.account_snapshot(positions)
    assert snap["sharpe"] is None and snap["sharpe_days"] is None


def test_realized_sharpe_gates_short_span():
    # 5 trade dates but all inside two weeks → None (span < 30 days).
    for day in ["2026-06-01", "2026-06-03", "2026-06-05", "2026-06-09", "2026-06-12"]:
        rec = pnl_service.create(_make_option())
        pnl_service.close(rec["id"], exit_price=8.10, exit_date=day)
    assert pnl_service.realized_sharpe(pnl_service.get_all()) is None


def test_realized_sharpe_positive_for_winning_history():
    # 6 winning closes spread over ~10 weeks → a real, positive Sharpe.
    days = ["2026-04-06", "2026-04-20", "2026-05-04", "2026-05-18", "2026-06-01", "2026-06-15"]
    for day in days:
        rec = pnl_service.create(_make_option())  # entry 5.40, qty 2
        pnl_service.close(rec["id"], exit_price=8.10, exit_date=day)  # +540 each
    positions = pnl_service.get_all()
    stats = pnl_service.realized_sharpe(positions)
    assert stats is not None
    assert stats["sharpe"] > 0
    # Weekday grid from first to last close (inclusive) — 51 weekdays.
    assert stats["days"] == 51
    snap = pnl_service.account_snapshot(positions)
    assert snap["sharpe"] == stats["sharpe"]
    assert snap["sharpe_days"] == 51


def test_realized_sharpe_none_when_flat():
    # Breakeven closes → zero-variance returns → None, not a NaN/inf Sharpe.
    days = ["2026-04-06", "2026-04-20", "2026-05-04", "2026-05-18", "2026-06-01"]
    for day in days:
        rec = pnl_service.create(_make_option())
        pnl_service.close(rec["id"], exit_price=5.40, exit_date=day)  # +0 each
    assert pnl_service.realized_sharpe(pnl_service.get_all()) is None


def test_instrument_key_distinguishes_contracts():
    call = _make_option()
    put = _make_option(option={"type": "put", "strike": 200.0, "expiration": "2026-07-17", "contract_ticker": None})
    stock = {"kind": "stock", "ticker": "NVDA"}
    keys = {pnl_service.instrument_key(call), pnl_service.instrument_key(put), pnl_service.instrument_key(stock)}
    assert len(keys) == 3
