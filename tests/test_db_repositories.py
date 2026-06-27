"""Behavior tests for the multi-tenant DB repositories (Phase 2 storage layer).

Runs against an in-memory SQLite database (StaticPool so the connection is
shared across the session). Pins each repository's CRUD semantics AND the
user-scoping guarantee — the whole point of the migration is that user A never
sees user B's data. Mirrors the file-service shapes the repos replace.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend.database.connection import Base
import app.backend.database.app_models  # noqa: F401  (register tables on Base)
from app.backend.repositories.watchlist_repository import WatchlistRepository
from app.backend.repositories.portfolio_repository import PortfolioRepository
from app.backend.repositories.portfolio_settings_repository import PortfolioSettingsRepository
from app.backend.repositories.pnl_repository import PnlRepository
from app.backend.repositories.thesis_repository import ThesisRepository
from app.backend.repositories.scan_repository import ScanRepository


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ─── Watchlists ──────────────────────────────────────────────────────────────

def test_watchlist_crud_and_shape(db):
    repo = WatchlistRepository(db, user_id="u1")
    assert repo.get_all() == []
    out = repo.upsert("Tech", [{"ticker": "NVDA", "comment": "ai"}])
    assert out == {"name": "Tech", "tickers": [{"ticker": "NVDA", "comment": "ai"}]}
    # upsert replaces
    repo.upsert("Tech", [{"ticker": "AAPL", "comment": ""}])
    assert repo.get_one("Tech")["tickers"] == [{"ticker": "AAPL", "comment": ""}]
    assert len(repo.get_all()) == 1
    assert repo.rename("Tech", "Megacap") is True
    assert repo.get_one("Tech") is None and repo.get_one("Megacap") is not None
    assert repo.delete("Megacap") is True
    assert repo.get_all() == [] and repo.delete("Megacap") is False


def test_watchlist_rename_conflict(db):
    repo = WatchlistRepository(db, user_id="u1")
    repo.upsert("A", [])
    repo.upsert("B", [])
    with pytest.raises(ValueError):
        repo.rename("A", "B")


def test_watchlist_user_scoping(db):
    a = WatchlistRepository(db, user_id="alice")
    b = WatchlistRepository(db, user_id="bob")
    a.upsert("Mine", [{"ticker": "TSLA", "comment": ""}])
    assert b.get_all() == []          # bob can't see alice's
    assert b.get_one("Mine") is None
    assert b.delete("Mine") is False  # bob can't delete alice's
    assert a.get_one("Mine") is not None


# ─── Portfolios (sleeves) ────────────────────────────────────────────────────

_SLEEVE = {"allocation_pct": 0.0, "agents": ["aswath_damodaran"],
           "agent_weights": {"aswath_damodaran": 1.0}, "tickers": ["NVDA", "MSFT"]}


def test_portfolio_crud(db):
    repo = PortfolioRepository(db, user_id="u1")
    repo.create_sleeve("individual", _SLEEVE)
    sleeves = repo.read_sleeves()
    assert sleeves["individual"] == _SLEEVE
    # update
    repo.update_sleeve("individual", {**_SLEEVE, "tickers": ["GOOG"]})
    assert repo.read_sleeves()["individual"]["tickers"] == ["GOOG"]
    # rename
    repo.rename_sleeve("individual", "roth")
    assert "roth" in repo.read_sleeves() and "individual" not in repo.read_sleeves()


def test_portfolio_conflicts_and_guards(db):
    repo = PortfolioRepository(db, user_id="u1")
    repo.create_sleeve("a", _SLEEVE)
    with pytest.raises(ValueError):      # duplicate name
        repo.create_sleeve("a", _SLEEVE)
    with pytest.raises(LookupError):     # update missing
        repo.update_sleeve("nope", _SLEEVE)
    with pytest.raises(ValueError):      # can't delete the last one
        repo.delete_sleeve("a")
    repo.create_sleeve("b", _SLEEVE)
    assert "a" not in repo.delete_sleeve("a")  # now deletable


def test_portfolio_replace_all_and_cash_reserve(db):
    repo = PortfolioRepository(db, user_id="u1")
    repo.create_sleeve("old", _SLEEVE)
    result = repo.replace_all_sleeves({"x": _SLEEVE, "y": _SLEEVE})
    assert set(result.keys()) == {"x", "y"}
    assert repo.get_cash_reserve() == 10.0   # default
    assert repo.set_cash_reserve(5.0) == 5.0
    assert repo.get_cash_reserve() == 5.0


# ─── Portfolio settings ──────────────────────────────────────────────────────

def test_portfolio_settings(db):
    repo = PortfolioSettingsRepository(db, user_id="u1")
    repo.upsert_ticker("individual", "NVDA", 20.0, None)
    repo.upsert_ticker("individual", "AAPL", 10.0, ["alpha_seeker"])
    allset = repo.get_all()
    assert allset["individual"]["NVDA"] == {"allocation_pct": 20.0, "agents": None}
    assert allset["individual"]["AAPL"] == {"allocation_pct": 10.0, "agents": ["alpha_seeker"]}
    # update in place
    repo.upsert_ticker("individual", "NVDA", 25.0, None)
    assert repo.get_sleeve("individual")["NVDA"]["allocation_pct"] == 25.0
    repo.delete_ticker("individual", "NVDA")
    assert "NVDA" not in repo.get_sleeve("individual")
    # put_all replaces wholesale
    repo.put_all({"roth": {"GOOG": {"allocation_pct": 50.0, "agents": None}}})
    assert repo.get_all() == {"roth": {"GOOG": {"allocation_pct": 50.0, "agents": None}}}


# ─── P&L positions ───────────────────────────────────────────────────────────

def _pos(**over):
    base = {
        "id": "pos_1", "kind": "option", "ticker": "NVDA", "side": "long", "qty": 2.0,
        "option": {"type": "call", "strike": 200.0, "expiration": "2026-07-17", "contract_ticker": None},
        "entry_price": 5.4, "entry_date": "2026-06-10", "status": "open",
        "exit_price": None, "exit_date": None, "source": "manual", "real": False,
        "notes": "", "import_key": None, "closing_import_key": None,
        "created_at": "2026-06-10T21:08:13+00:00", "updated_at": "2026-06-10T21:08:13+00:00",
    }
    base.update(over)
    return base


def test_pnl_crud_and_import_keys(db):
    repo = PnlRepository(db, user_id="u1")
    repo.insert(_pos())
    repo.insert(_pos(id="pos_2", ticker="AAPL", import_key="k2", created_at="2026-06-11T00:00:00+00:00"))
    rows = repo.get_all()
    assert [r["id"] for r in rows] == ["pos_1", "pos_2"]   # ordered by created_at
    assert rows[0]["option"]["strike"] == 200.0           # JSON round-trips
    # update / close-like patch
    repo.update("pos_1", {"status": "closed", "exit_price": 9.0, "closing_import_key": "kc1"})
    assert repo.get("pos_1")["status"] == "closed"
    assert repo.existing_import_keys() == {"k2", "kc1"}
    assert repo.delete("pos_1") is True
    assert repo.update("pos_1", {"notes": "x"}) is None    # gone
    assert repo.bulk_insert([_pos(id="pos_3"), _pos(id="pos_4")]) == 2
    assert {r["id"] for r in repo.get_all()} == {"pos_2", "pos_3", "pos_4"}


def test_pnl_user_scoping(db):
    PnlRepository(db, user_id="alice").insert(_pos(id="pos_a"))
    bob = PnlRepository(db, user_id="bob")
    assert bob.get_all() == []
    assert bob.get("pos_a") is None
    assert bob.delete("pos_a") is False


# ─── Theses ──────────────────────────────────────────────────────────────────

def test_thesis_upsert(db):
    repo = ThesisRepository(db, user_id="u1")
    repo.upsert("portfolio", {"condensed": "bullish", "saved_at": "t0"})
    repo.upsert("ticker:NVDA:quick", {"condensed": "buy", "saved_at": "t1"})
    allt = repo.get_all()
    assert set(allt.keys()) == {"portfolio", "ticker:NVDA:quick"}
    repo.upsert("portfolio", {"condensed": "bearish", "saved_at": "t2"})  # replace
    assert repo.get("portfolio")["condensed"] == "bearish"


# ─── Scan results ────────────────────────────────────────────────────────────

def test_scan_results(db):
    repo = ScanRepository(db, user_id="u1")
    repo.upsert("2026-06-25", {"date": "2026-06-25", "rows": [{"ticker": "NVDA"}]})
    repo.upsert("2026-06-26", {"date": "2026-06-26", "rows": []})
    assert repo.list_dates() == ["2026-06-26", "2026-06-25"]   # newest first
    assert repo.latest()["date"] == "2026-06-26"
    assert repo.get("2026-06-25")["rows"] == [{"ticker": "NVDA"}]
    repo.upsert("2026-06-25", {"date": "2026-06-25", "rows": [{"ticker": "AAPL"}]})  # replace
    assert repo.get("2026-06-25")["rows"] == [{"ticker": "AAPL"}]
    assert len(repo.list_dates()) == 2   # still 2 (upsert, not insert)
    # list_scans returns the route-shaped entries (synthetic path, no size).
    entries = repo.list_scans()
    assert entries[0] == {"date": "2026-06-26", "path": "db://scan/2026-06-26", "size_bytes": None}


def test_json_in_place_mutation_persists(db):
    """C1 regression: mutating a JSON column in place (not reassigning) must
    still persist, thanks to the MutableList/MutableDict wrappers."""
    repo = PortfolioRepository(db, user_id="u1")
    repo.create_sleeve("s", _SLEEVE)
    p = repo._get("s")            # ORM object
    p.tickers.append("ZZZZ")      # in-place mutation, no reassignment
    p.agent_weights["new"] = 0.5  # in-place dict mutation
    db.commit()
    db.expire_all()               # force a fresh read from the DB
    reloaded = repo.read_sleeves()["s"]
    assert "ZZZZ" in reloaded["tickers"]
    assert reloaded["agent_weights"].get("new") == 0.5
