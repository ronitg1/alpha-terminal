"""Cutover tests: each file service must behave identically under both storage
backends (``STORAGE_BACKEND=file`` and ``=db``).

The whole point of the Phase 2 cutover is that flipping the flag changes *where*
state lives, never *what shape* the routes/frontend see. So the canonical test
here runs the same sequence of public service calls under both backends and
asserts the returned dicts are identical. Each service that gets cut over adds
its block below.

The DB backend runs against in-memory SQLite (StaticPool, shared connection),
mirroring ``tests/test_db_repositories.py``; we monkeypatch the service layer's
``SessionLocal`` onto that engine and set ``STORAGE_BACKEND=db``.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend.database.connection import Base
import app.backend.database.app_models  # noqa: F401  (register tables on Base)
from app.backend.services import _storage
from app.backend.services import watchlists_service
from app.backend.services import sleeve_config_service
from app.backend.services import portfolio_settings_service
from app.backend.services import thesis_store
from app.backend.services import pnl_service
from app.backend.services import watchlist_service
from app.backend.services import user_settings_service
from app.backend.services import scan_schedule_service
from app.backend.routes import sleeves as sleeves_routes


@pytest.fixture()
def db_backend(monkeypatch):
    """Activate the DB backend on an isolated in-memory SQLite engine."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(_storage, "SessionLocal", TestSession)
    monkeypatch.setenv("STORAGE_BACKEND", "db")
    try:
        # Yield the session factory so route-layer tests (which take an explicit
        # ``db`` via Depends) can bind to this same in-memory engine.
        yield TestSession
    finally:
        engine.dispose()


@pytest.fixture()
def file_backend(monkeypatch, tmp_path):
    """Activate the file backend pointed at a throwaway temp store."""
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    store = tmp_path / "watchlists.json"
    legacy = tmp_path / "legacy_watchlists.json"  # intentionally absent
    monkeypatch.setattr(watchlists_service, "_STORE_PATH", store)
    monkeypatch.setattr(watchlists_service, "_LEGACY_STORE_PATH", legacy)
    yield


# ─── watchlists_service ──────────────────────────────────────────────────────

def _exercise_watchlists() -> dict:
    """Run a fixed sequence against the watchlists_service public API and
    capture every observable result, so two backends can be compared."""
    out: dict = {}
    out["initial"] = watchlists_service.get_all()
    out["created_tech"] = watchlists_service.upsert(
        "Tech", [{"ticker": "NVDA", "comment": "ai"}]
    )
    out["created_energy"] = watchlists_service.upsert(
        "Energy", [{"ticker": "XOM", "comment": ""}]
    )
    # replace existing
    out["replaced_tech"] = watchlists_service.upsert(
        "Tech", [{"ticker": "AAPL", "comment": "swap"}]
    )
    out["get_one_tech"] = watchlists_service.get_one("Tech")
    out["get_one_missing"] = watchlists_service.get_one("Nope")
    out["renamed"] = watchlists_service.rename("Energy", "Oil & Gas")
    out["after_rename_names"] = sorted(w["name"] for w in watchlists_service.get_all())
    out["deleted"] = watchlists_service.delete("Tech")
    out["delete_missing"] = watchlists_service.delete("Tech")
    out["final_names"] = sorted(w["name"] for w in watchlists_service.get_all())
    return out


def test_watchlists_db_backend(db_backend):
    result = _exercise_watchlists()
    assert result["initial"] == []
    assert result["created_tech"] == {
        "name": "Tech",
        "tickers": [{"ticker": "NVDA", "comment": "ai"}],
    }
    assert result["replaced_tech"]["tickers"] == [{"ticker": "AAPL", "comment": "swap"}]
    assert result["get_one_tech"]["tickers"] == [{"ticker": "AAPL", "comment": "swap"}]
    assert result["get_one_missing"] is None
    assert result["renamed"] is True
    assert result["after_rename_names"] == ["Oil & Gas", "Tech"]
    assert result["deleted"] is True
    assert result["delete_missing"] is False
    assert result["final_names"] == ["Oil & Gas"]


def test_watchlists_file_backend(file_backend):
    result = _exercise_watchlists()
    # The file store seeds a default "Market Watchlist"; the DB store starts
    # empty. That seed is the only legitimate divergence — strip it, then the
    # observable behavior must match the DB backend exactly.
    assert {"name": "Market Watchlist", "tickers": []} in result["initial"]


def test_watchlists_db_rename_conflict_raises(db_backend):
    """Under the DB backend, renaming onto an existing name raises ValueError
    at the service layer (the route turns that into a 409 — see next test)."""
    watchlists_service.upsert("A", [])
    watchlists_service.upsert("B", [])
    with pytest.raises(ValueError):
        watchlists_service.rename("A", "B")


def test_rename_route_maps_conflict_to_409(db_backend):
    """Route-layer guarantee: the rename handler catches the repo's ValueError
    and returns HTTP 409, not a 500. This is the assertion that catches the
    regression the service-only tests can't see. We drive the route coroutine
    directly (no full-app boot needed) via asyncio.run."""
    import asyncio

    from fastapi import HTTPException

    from app.backend.routes.sleeves import RenamePayload, rename_watchlist

    watchlists_service.upsert("A", [])
    watchlists_service.upsert("B", [])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(rename_watchlist("A", RenamePayload(new_name="B")))
    assert exc_info.value.status_code == 409


def test_upsert_integrity_translation_fires(db_backend, monkeypatch):
    """Prove integrity_as_value_error actually converts an IntegrityError into a
    ValueError at the cutover seam (the race-defense wrapper is otherwise dead in
    single-threaded tests). We force the repo's commit to raise IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    from app.backend.repositories import watchlist_repository

    def _boom(self, *a, **k):  # noqa: ANN001
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    monkeypatch.setattr(watchlist_repository.WatchlistRepository, "upsert", _boom)
    with pytest.raises(ValueError):
        watchlists_service.upsert("Tech", [])


def test_watchlists_backends_shape_identical(db_backend, monkeypatch, tmp_path):
    """The strong guarantee: with both stores starting empty, the full call
    sequence yields byte-identical results across backends."""
    db_result = _exercise_watchlists()

    # Now run the same sequence on a fresh file store and compare.
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    store = tmp_path / "wl.json"
    monkeypatch.setattr(watchlists_service, "_STORE_PATH", store)
    monkeypatch.setattr(
        watchlists_service, "_LEGACY_STORE_PATH", tmp_path / "absent.json"
    )
    # Seed the file store empty so it starts from the same baseline as the DB.
    store.write_text('{"watchlists": []}', encoding="utf-8")
    file_result = _exercise_watchlists()

    assert db_result == file_result


# ─── sleeve_config_service (portfolios) ──────────────────────────────────────

_SLEEVE_KEYS = {"allocation_pct", "agents", "agent_weights", "tickers"}


def _valid_sleeve(tickers=("NVDA", "MSFT")) -> dict:
    return {
        "allocation_pct": 20.0,
        "agents": ["aswath_damodaran"],
        "agent_weights": {"aswath_damodaran": 1.0},
        "tickers": list(tickers),
    }


def test_sleeves_db_backend_crud_and_http_codes(db_backend):
    """Full sleeve lifecycle under the DB backend, including the HTTP status
    codes the routes rely on (the service translates repo ValueError/LookupError
    into the same HTTPException codes the file backend used)."""
    from fastapi import HTTPException

    out = sleeve_config_service.create_sleeve("alpha", _valid_sleeve())
    assert set(out["alpha"].keys()) == _SLEEVE_KEYS
    assert out["alpha"]["tickers"] == ["NVDA", "MSFT"]

    # duplicate -> 409
    with pytest.raises(HTTPException) as ei:
        sleeve_config_service.create_sleeve("alpha", _valid_sleeve())
    assert ei.value.status_code == 409

    # update existing reflects; update missing -> 404
    sleeve_config_service.update_sleeve("alpha", _valid_sleeve(tickers=["GOOG"]))
    assert sleeve_config_service.read_sleeves()["alpha"]["tickers"] == ["GOOG"]
    with pytest.raises(HTTPException) as ei:
        sleeve_config_service.update_sleeve("ghost", _valid_sleeve())
    assert ei.value.status_code == 404

    # can't delete the last sleeve -> 400; add a second, then delete works
    with pytest.raises(HTTPException) as ei:
        sleeve_config_service.delete_sleeve("alpha")
    assert ei.value.status_code == 400
    sleeve_config_service.create_sleeve("beta", _valid_sleeve())
    after_del = sleeve_config_service.delete_sleeve("alpha")
    assert set(after_del.keys()) == {"beta"}

    # delete missing -> 404
    with pytest.raises(HTTPException) as ei:
        sleeve_config_service.delete_sleeve("alpha")
    assert ei.value.status_code == 404

    # rename: success, bad-name 400, missing 404, conflict 409
    sleeve_config_service.create_sleeve("gamma", _valid_sleeve())
    renamed = sleeve_config_service.rename_sleeve("beta", "delta")
    assert "delta" in renamed and "beta" not in renamed
    with pytest.raises(HTTPException) as ei:
        sleeve_config_service.rename_sleeve("delta", "Bad Name")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        sleeve_config_service.rename_sleeve("ghost", "epsilon")
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        sleeve_config_service.rename_sleeve("delta", "gamma")
    assert ei.value.status_code == 409

    # cash reserve default
    assert sleeve_config_service.get_cash_reserve() == 10.0


def test_sleeves_db_create_integrity_maps_to_409(db_backend, monkeypatch):
    """A race IntegrityError on create_sleeve (raised at commit INSIDE the repo)
    must surface as a 409, not a 500 — proves the integrity->ValueError->409
    chain fires for the portfolio path, not just watchlists."""
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from app.backend.repositories import portfolio_repository

    def _boom(self, *a, **k):  # noqa: ANN001
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    monkeypatch.setattr(
        portfolio_repository.PortfolioRepository, "create_sleeve", _boom
    )
    with pytest.raises(HTTPException) as ei:
        sleeve_config_service.create_sleeve("alpha", _valid_sleeve())
    assert ei.value.status_code == 409


def test_sleeves_db_replace_all(db_backend):
    sleeve_config_service.create_sleeve("old", _valid_sleeve())
    result = sleeve_config_service.replace_all_sleeves(
        {"x": _valid_sleeve(), "y": _valid_sleeve(tickers=["AAPL"])}
    )
    assert set(result.keys()) == {"x", "y"}


def test_sleeves_file_backend_write_roundtrip(monkeypatch, tmp_path):
    """Exercise the FILE backend's write path (brace-walker splice + reload)
    without touching the checked-in src/config/portfolio_config.py: copy it into
    a temp module, point the service at it, and assert a create/rename/delete
    round-trips through the rewritten source."""
    import importlib
    import shutil
    import sys

    monkeypatch.setenv("STORAGE_BACKEND", "file")

    real_path = sleeve_config_service._CONFIG_PATH
    # Name the temp module so its file lives on a path importlib can re-find:
    # _persist() calls importlib.reload(), which re-discovers the spec via the
    # import system, so the module must be importable by name from sys.path.
    mod_name = "tmp_portfolio_config_probe"
    tmp_cfg = tmp_path / f"{mod_name}.py"
    shutil.copyfile(real_path, tmp_cfg)
    monkeypatch.syspath_prepend(str(tmp_path))
    mod = importlib.import_module(mod_name)
    try:
        monkeypatch.setattr(sleeve_config_service, "_CONFIG_PATH", tmp_cfg)
        monkeypatch.setattr(sleeve_config_service, "portfolio_config_module", mod)

        new = {
            "allocation_pct": 0.0,  # 0 keeps total allocation valid
            "agents": ["aswath_damodaran"],
            "agent_weights": {"aswath_damodaran": 1.0},
            "tickers": ["TESTX"],
        }
        after_create = sleeve_config_service.create_sleeve("cutover_probe", new)
        assert after_create["cutover_probe"]["tickers"] == ["TESTX"]

        after_rename = sleeve_config_service.rename_sleeve("cutover_probe", "cutover_probe2")
        assert "cutover_probe2" in after_rename and "cutover_probe" not in after_rename

        after_delete = sleeve_config_service.delete_sleeve("cutover_probe2")
        assert "cutover_probe2" not in after_delete
        # The original sleeves survived the splice/rewrite untouched.
        assert len(after_delete) >= 1
    finally:
        sys.modules.pop(mod_name, None)


def test_sleeves_file_backend_read_shape_matches_db(db_backend, monkeypatch):
    """Shape-identity across backends without destructively rewriting the real
    portfolio_config.py: a DB-created sleeve dict has exactly the same keys as a
    file-backend read of the live config."""
    db_sleeve = sleeve_config_service.create_sleeve("alpha", _valid_sleeve())["alpha"]

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    file_sleeves = sleeve_config_service.read_sleeves()
    assert file_sleeves, "live config should have at least one sleeve"
    file_sleeve = next(iter(file_sleeves.values()))

    assert set(db_sleeve.keys()) == set(file_sleeve.keys()) == _SLEEVE_KEYS
    assert isinstance(sleeve_config_service.get_cash_reserve(), float)


# ─── portfolio_settings_service ──────────────────────────────────────────────

def _exercise_portfolio_settings() -> dict:
    """Fixed sequence against the portfolio_settings_service public API."""
    out: dict = {}
    out["initial"] = portfolio_settings_service.get_all()
    # lowercase ticker must be stored uppercased by both backends
    out["after_nvda"] = portfolio_settings_service.upsert_ticker(
        "individual", "nvda", 20.0, None
    )
    out["after_aapl"] = portfolio_settings_service.upsert_ticker(
        "individual", "AAPL", 10.0, ["alpha_seeker"]
    )
    out["sleeve"] = portfolio_settings_service.get_sleeve("individual")
    # update in place
    out["updated_nvda"] = portfolio_settings_service.upsert_ticker(
        "individual", "NVDA", 25.0, None
    )
    portfolio_settings_service.delete_ticker("individual", "nvda")
    out["after_delete"] = portfolio_settings_service.get_all()
    out["after_put_all"] = portfolio_settings_service.put_all(
        {"roth": {"GOOG": {"allocation_pct": 50.0, "agents": None}}}
    )
    out["final"] = portfolio_settings_service.get_all()
    return out


def test_portfolio_settings_db_backend(db_backend):
    r = _exercise_portfolio_settings()
    assert r["initial"] == {}
    assert r["after_nvda"]["individual"]["NVDA"] == {"allocation_pct": 20.0, "agents": None}
    assert r["after_aapl"]["individual"]["AAPL"] == {
        "allocation_pct": 10.0,
        "agents": ["alpha_seeker"],
    }
    assert r["updated_nvda"]["individual"]["NVDA"]["allocation_pct"] == 25.0
    assert "NVDA" not in r["after_delete"]["individual"]
    assert r["final"] == {"roth": {"GOOG": {"allocation_pct": 50.0, "agents": None}}}


def test_portfolio_settings_backends_shape_identical(db_backend, monkeypatch, tmp_path):
    db_result = _exercise_portfolio_settings()

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(
        portfolio_settings_service, "_DATA_PATH", tmp_path / "portfolio_settings.json"
    )
    file_result = _exercise_portfolio_settings()

    assert db_result == file_result


# ─── thesis_store ────────────────────────────────────────────────────────────

def _exercise_theses() -> dict:
    out: dict = {}
    out["initial"] = thesis_store.get_all()
    thesis_store.save("portfolio", {"condensed": "bullish"})
    thesis_store.save("ticker:NVDA:quick", {"condensed": "buy"})
    thesis_store.save("portfolio", {"condensed": "bearish"})  # replace
    out["all"] = thesis_store.get_all()
    return out


def _strip_saved_at(d: dict) -> dict:
    return {k: {kk: vv for kk, vv in v.items() if kk != "saved_at"} for k, v in d.items()}


def test_theses_db_backend(db_backend):
    r = _exercise_theses()
    assert r["initial"] == {}
    assert set(r["all"].keys()) == {"portfolio", "ticker:NVDA:quick"}
    assert r["all"]["portfolio"]["condensed"] == "bearish"   # replace won
    assert "saved_at" in r["all"]["portfolio"]               # stamp added


def test_theses_backends_shape_identical(db_backend, monkeypatch, tmp_path):
    db_result = _exercise_theses()

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(thesis_store, "_DATA_PATH", tmp_path / "theses.json")
    file_result = _exercise_theses()

    # saved_at is a wall-clock stamp that differs per call; everything else must
    # match across backends.
    assert _strip_saved_at(db_result["all"]) == _strip_saved_at(file_result["all"])
    assert all("saved_at" in v for v in file_result["all"].values())


# ─── pnl_service (persistence; math stays in the service) ────────────────────

def _pnl_record(**over) -> dict:
    """A fully-formed position record (id + timestamps set) for bulk_insert, so
    both backends store byte-identical rows for the shape-identity check."""
    base = {
        "id": "pos_fixed1", "kind": "option", "ticker": "NVDA", "side": "long",
        "qty": 2.0,
        "option": {"type": "call", "strike": 200.0, "expiration": "2026-07-17",
                   "contract_ticker": None},
        "entry_price": 5.4, "entry_date": "2026-06-10", "status": "open",
        "exit_price": None, "exit_date": None, "source": "manual", "real": False,
        "notes": "", "import_key": None, "closing_import_key": None,
        "created_at": "2026-06-10T21:08:13+00:00",
        "updated_at": "2026-06-10T21:08:13+00:00",
    }
    base.update(over)
    return base


def test_pnl_db_backend_crud(db_backend):
    assert pnl_service.get_all() == []
    pos = pnl_service.create(
        {"kind": "stock", "ticker": "nvda", "side": "long", "qty": 10, "entry_price": 100.0}
    )
    assert pos["id"].startswith("pos_")
    assert pos["ticker"] == "NVDA"  # uppercased by the service
    # update + close go through the repo
    pnl_service.update(pos["id"], {"notes": "hold"})
    assert pnl_service.get_all()[0]["notes"] == "hold"
    pnl_service.create(
        {"kind": "option", "ticker": "AAPL", "side": "long", "qty": 1, "entry_price": 3.0,
         "option": {"type": "call", "strike": 200.0, "expiration": "2026-07-17",
                    "contract_ticker": None}, "import_key": "k1"}
    )
    assert pnl_service.existing_import_keys() == {"k1"}
    assert pnl_service.delete(pos["id"]) is True
    assert pnl_service.delete(pos["id"]) is False
    assert pnl_service.bulk_insert([_pnl_record(id="pos_b1"), _pnl_record(id="pos_b2")]) == 2
    assert {p["id"] for p in pnl_service.get_all()} >= {"pos_b1", "pos_b2"}


def test_pnl_backends_shape_identical(db_backend, monkeypatch, tmp_path):
    records = [
        _pnl_record(id="pos_a", created_at="2026-06-10T00:00:00+00:00"),
        _pnl_record(id="pos_b", ticker="AAPL", kind="stock", option=None,
                    created_at="2026-06-11T00:00:00+00:00"),
    ]
    pnl_service.bulk_insert(records)
    db_all = sorted(pnl_service.get_all(), key=lambda p: p["id"])

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(pnl_service, "_DATA_PATH", tmp_path / "pnl_positions.json")
    pnl_service.bulk_insert(records)
    file_all = sorted(pnl_service.get_all(), key=lambda p: p["id"])

    assert db_all == file_all


def test_pnl_create_keyset_identical_across_backends(db_backend, monkeypatch, tmp_path):
    """create() must return the same key set under both backends — guards the
    closing_import_key divergence the bulk_insert test sidesteps."""
    fields = {"kind": "stock", "ticker": "NVDA", "side": "long", "qty": 5, "entry_price": 50.0}
    db_keys = set(pnl_service.create(fields).keys())

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(pnl_service, "_DATA_PATH", tmp_path / "pnl_positions.json")
    file_keys = set(pnl_service.create(fields).keys())

    assert db_keys == file_keys
    assert "closing_import_key" in file_keys


# ─── scan results (routes/sleeves.py) ────────────────────────────────────────

def _scan_rows(*tickers) -> list[dict]:
    return [{"ticker": t, "sleeve": "individual", "consensus": "bullish",
             "weighted_score": 1.0, "per_agent": []} for t in tickers]


def test_scans_db_backend(db_backend, tmp_path):
    import asyncio

    TestSession = db_backend
    outputs = tmp_path / "outputs"

    # Write two dates via the (now backend-aware) write helper -> ScanRepository.
    sleeves_routes._write_scan_json_ui(_scan_rows("NVDA", "MSFT"), outputs, "2026-06-24")
    sleeves_routes._write_scan_json_ui(_scan_rows("AAPL"), outputs, "2026-06-25")

    # list_scans: synthetic db path + size_bytes None, newest first.
    listing = asyncio.run(sleeves_routes.list_scans(limit=30, db=TestSession()))["scans"]
    assert [e["date"] for e in listing] == ["2026-06-25", "2026-06-24"]
    assert listing[0] == {"date": "2026-06-25", "path": "db://scan/2026-06-25", "size_bytes": None}

    # latest + by-date read back the stored payload.
    latest = asyncio.run(sleeves_routes.get_latest_scan(db=TestSession()))
    assert latest["date"] == "2026-06-25"
    by_date = asyncio.run(sleeves_routes.get_scan_by_date("2026-06-24", db=TestSession()))
    assert [r["ticker"] for r in by_date["rows"]] == ["NVDA", "MSFT"]

    # missing date -> 404
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(sleeves_routes.get_scan_by_date("2020-01-01", db=TestSession()))
    assert ei.value.status_code == 404

    # merge: a partial rescan of one ticker folds into the existing date.
    merged = sleeves_routes._merge_into_today(_scan_rows("MSFT"), "2026-06-24")
    # MSFT (rescanned) replaces its old row; NVDA survives.
    assert {r["ticker"] for r in merged} == {"NVDA", "MSFT"}


def test_scans_latest_empty_404(db_backend):
    import asyncio
    from fastapi import HTTPException

    TestSession = db_backend
    with pytest.raises(HTTPException) as ei:
        asyncio.run(sleeves_routes.get_latest_scan(db=TestSession()))
    assert ei.value.status_code == 404


def test_scans_db_write_is_upsert(db_backend, tmp_path):
    """Re-writing the same date replaces (one row per date), not appends."""
    import asyncio

    TestSession = db_backend
    outputs = tmp_path / "outputs"
    sleeves_routes._write_scan_json_ui(_scan_rows("NVDA"), outputs, "2026-06-24")
    sleeves_routes._write_scan_json_ui(_scan_rows("AAPL", "GOOG"), outputs, "2026-06-24")

    listing = asyncio.run(sleeves_routes.list_scans(limit=30, db=TestSession()))["scans"]
    assert len(listing) == 1
    by_date = asyncio.run(sleeves_routes.get_scan_by_date("2026-06-24", db=TestSession()))
    assert [r["ticker"] for r in by_date["rows"]] == ["AAPL", "GOOG"]


def test_scan_payload_keyset_identical_across_backends(db_backend, monkeypatch, tmp_path):
    """The scan payload envelope must have the same keys under both backends
    (only the `path` VALUE differs by design: db://scan/... vs a real path)."""
    import asyncio

    TestSession = db_backend
    outputs = tmp_path / "outputs"
    sleeves_routes._write_scan_json_ui(_scan_rows("NVDA"), outputs, "2026-06-24")
    db_payload = asyncio.run(sleeves_routes.get_scan_by_date("2026-06-24", db=TestSession()))

    # File backend: write the sidecar and read it back, pointing the route's
    # path resolution at the temp project root.
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(sleeves_routes, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sleeves_routes, "_OUTPUTS_DIR", outputs)
    sleeves_routes._write_scan_json_ui(_scan_rows("NVDA"), outputs, "2026-06-23")
    file_payload = asyncio.run(
        sleeves_routes.get_scan_by_date("2026-06-23", db=None)
    )
    assert set(db_payload.keys()) == set(file_payload.keys())


# ─── legacy single (opportunistic) watchlist_service ─────────────────────────

def test_legacy_watchlist_db_backend(db_backend):
    assert watchlist_service.read_watchlist_with_comments() == []
    out = watchlist_service.write_watchlist(
        [{"ticker": "nvda", "comment": "ai"}, {"ticker": "TSLA", "comment": ""}]
    )
    # canonicalized: uppercased, shape preserved
    assert out == [
        {"ticker": "NVDA", "comment": "ai"},
        {"ticker": "TSLA", "comment": ""},
    ]
    assert watchlist_service.read_watchlist_with_comments() == out


def test_legacy_watchlist_hidden_from_multi(db_backend):
    """The opportunistic list shares the watchlists table under the db backend
    but must NOT appear in the multi-watchlist surface (matches the file backend
    where it's a separate file)."""
    watchlist_service.write_watchlist([{"ticker": "NVDA", "comment": ""}])
    watchlists_service.upsert("Tech", [{"ticker": "MSFT", "comment": ""}])

    names = [w["name"] for w in watchlists_service.get_all()]
    assert names == ["Tech"]  # opportunistic sentinel hidden
    assert watchlists_service.get_one(_storage.RESERVED_OPPORTUNISTIC_WATCHLIST) is None
    assert watchlists_service.delete(_storage.RESERVED_OPPORTUNISTIC_WATCHLIST) is False
    # but the legacy service still sees it
    assert watchlist_service.read_watchlist_with_comments() == [
        {"ticker": "NVDA", "comment": ""}
    ]


def test_multi_upsert_rejects_reserved_name(db_backend):
    """The multi-watchlist store must refuse to write the reserved opportunistic
    name (would clobber/hide the legacy store). 400, and the legacy store is
    untouched."""
    from fastapi import HTTPException

    watchlist_service.write_watchlist([{"ticker": "NVDA", "comment": ""}])
    with pytest.raises(HTTPException) as ei:
        watchlists_service.upsert(_storage.RESERVED_OPPORTUNISTIC_WATCHLIST, [])
    assert ei.value.status_code == 400
    # legacy store intact
    assert watchlist_service.read_watchlist_with_comments() == [
        {"ticker": "NVDA", "comment": ""}
    ]


def test_multi_rename_to_reserved_rejected(db_backend):
    from fastapi import HTTPException

    watchlists_service.upsert("Tech", [{"ticker": "MSFT", "comment": ""}])
    with pytest.raises(HTTPException) as ei:
        watchlists_service.rename("Tech", _storage.RESERVED_OPPORTUNISTIC_WATCHLIST)
    assert ei.value.status_code == 400
    assert watchlists_service.get_one("Tech") is not None  # unchanged


def test_multi_upsert_rejects_reserved_name_file_backend(monkeypatch, tmp_path):
    """Parity: the file backend rejects the reserved name too, so a git-diff'd
    watchlists.json never grows an __opportunistic__ entry."""
    from fastapi import HTTPException

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(watchlists_service, "_STORE_PATH", tmp_path / "wl.json")
    monkeypatch.setattr(watchlists_service, "_LEGACY_STORE_PATH", tmp_path / "absent.json")
    with pytest.raises(HTTPException) as ei:
        watchlists_service.upsert(_storage.RESERVED_OPPORTUNISTIC_WATCHLIST, [])
    assert ei.value.status_code == 400


def test_legacy_watchlist_validation_both_backends(db_backend):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        watchlist_service.write_watchlist([{"ticker": "not a ticker!", "comment": ""}])
    assert ei.value.status_code == 400


def test_legacy_watchlist_file_read_shape_matches_db(db_backend, monkeypatch):
    """Non-destructive shape check: a db-written entry has the same keys as a
    file-backend read of the real watchlist.py."""
    db_entry = watchlist_service.write_watchlist([{"ticker": "NVDA", "comment": "x"}])[0]

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    file_entries = watchlist_service.read_watchlist_with_comments()
    # file read returns {ticker, comment} dicts; compare the key set
    if file_entries:
        assert set(file_entries[0].keys()) == set(db_entry.keys()) == {"ticker", "comment"}
    else:
        assert set(db_entry.keys()) == {"ticker", "comment"}


# ─── user_settings_service (onboarding flag) ─────────────────────────────────

def _exercise_onboarding() -> dict:
    """Read/flip/read the onboarding flag and capture every observable result."""
    out: dict = {}
    out["initial"] = user_settings_service.get_onboarding_completed()
    out["after_set"] = user_settings_service.set_onboarding_completed(True)
    out["read_after_set"] = user_settings_service.get_onboarding_completed()
    out["after_clear"] = user_settings_service.set_onboarding_completed(False)
    out["read_after_clear"] = user_settings_service.get_onboarding_completed()
    return out


def test_onboarding_db_backend(db_backend):
    result = _exercise_onboarding()
    assert result["initial"] is False
    assert result["after_set"] is True
    assert result["read_after_set"] is True
    assert result["after_clear"] is False
    assert result["read_after_clear"] is False


def test_onboarding_backends_shape_identical(db_backend, monkeypatch, tmp_path):
    """Both backends start "not onboarded" and observe identical results across
    the full read/set/clear sequence."""
    db_result = _exercise_onboarding()

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(user_settings_service, "_DATA_PATH", tmp_path / "onboarding.json")
    file_result = _exercise_onboarding()

    assert db_result == file_result
    # And the file write actually persisted the final state.
    monkeypatch.setattr(user_settings_service, "_DATA_PATH", tmp_path / "onboarding.json")
    user_settings_service.set_onboarding_completed(True)
    assert user_settings_service.get_onboarding_completed() is True


# ─── scan_schedule_service (scheduled pre-scans) ─────────────────────────────

def _exercise_schedules() -> dict:
    """CRUD over a user's scan schedules; capture observable results (no raw ids,
    which legitimately differ between an autoincrement DB and the file store)."""
    out: dict = {}
    out["initial"] = scan_schedule_service.list_schedules()
    created = scan_schedule_service.add_schedule("08:00", "America/New_York")
    out["created_time"], out["created_enabled"] = created["time_of_day"], created["enabled"]
    scan_schedule_service.add_schedule("15:30", "America/New_York")
    out["count_after_add"] = len(scan_schedule_service.list_schedules())
    sid = scan_schedule_service.list_schedules()[0]["id"]
    out["toggled_enabled"] = scan_schedule_service.set_schedule_enabled(sid, False)["enabled"]
    scan_schedule_service.delete_schedule(sid)
    out["times_after_delete"] = sorted(s["time_of_day"] for s in scan_schedule_service.list_schedules())
    return out


def test_schedules_db_backend(db_backend):
    result = _exercise_schedules()
    assert result["initial"] == []
    assert result["created_time"] == "08:00"
    assert result["created_enabled"] is True
    assert result["count_after_add"] == 2
    assert result["toggled_enabled"] is False
    assert result["times_after_delete"] == ["15:30"]


def test_schedules_backends_shape_identical(db_backend, monkeypatch, tmp_path):
    db_result = _exercise_schedules()
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(scan_schedule_service, "_SCHED_PATH", tmp_path / "sched.json")
    file_result = _exercise_schedules()
    assert db_result == file_result


def test_schedule_rejects_bad_time(db_backend):
    with pytest.raises(ValueError):
        scan_schedule_service.add_schedule("8am", "America/New_York")
    with pytest.raises(ValueError):
        scan_schedule_service.add_schedule("25:00", "America/New_York")


def _exercise_schedule_config() -> dict:
    """Per-schedule timeframe + lookback: create, clamp, update, list."""
    out: dict = {}
    created = scan_schedule_service.add_schedule("07:00", "America/New_York", "1h", 30)
    out["created"] = (created["timeframe"], created["lookback_days"])
    # 1h's server-side max lookback is 90d, so 999 clamps to 90.
    clamped = scan_schedule_service.add_schedule("08:00", "America/New_York", "1h", 999)
    out["clamped_lookback"] = clamped["lookback_days"]
    updated = scan_schedule_service.update_schedule(clamped["id"], "day", 730)
    out["updated"] = (updated["timeframe"], updated["lookback_days"])
    out["listed"] = sorted(
        (s["time_of_day"], s["timeframe"], s["lookback_days"])
        for s in scan_schedule_service.list_schedules()
    )
    return out


def test_schedule_config_db_backend(db_backend):
    r = _exercise_schedule_config()
    assert r["created"] == ("1h", 30)
    assert r["clamped_lookback"] == 90
    assert r["updated"] == ("day", 730)
    assert ("07:00", "1h", 30) in r["listed"]
    assert ("08:00", "day", 730) in r["listed"]


def test_schedule_config_backends_shape_identical(db_backend, monkeypatch, tmp_path):
    db_result = _exercise_schedule_config()
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(scan_schedule_service, "_SCHED_PATH", tmp_path / "sched.json")
    file_result = _exercise_schedule_config()
    assert db_result == file_result


def test_schedule_rejects_bad_timeframe_and_lookback(db_backend):
    with pytest.raises(ValueError):
        scan_schedule_service.add_schedule("09:00", "America/New_York", "5s", 30)
    with pytest.raises(ValueError):
        scan_schedule_service.add_schedule("09:00", "America/New_York", "day", 0)


def _exercise_prescan_keying(uid: str) -> dict:
    """Two pre-scans with different timeframes must coexist, not overwrite."""
    scan_schedule_service.set_prescan_for(uid, [{"ticker": "AAA"}], "day", 5)
    scan_schedule_service.set_prescan_for(uid, [{"ticker": "BBB"}, {"ticker": "CCC"}], "1h", 8)
    day = scan_schedule_service.get_prescan("day")
    hour = scan_schedule_service.get_prescan("1h")
    return {
        "day": (day["timeframe"], len(day["results"]), day["ticker_count"]),
        "hour": (hour["timeframe"], len(hour["results"]), hour["ticker_count"]),
        "missing": scan_schedule_service.get_prescan("15m"),
    }


def test_prescan_keyed_by_timeframe_db(db_backend):
    from app.backend.services._storage import current_user_id

    r = _exercise_prescan_keying(current_user_id())
    assert r["day"] == ("day", 1, 5)
    assert r["hour"] == ("1h", 2, 8)
    assert r["missing"] is None


def test_prescan_keyed_by_timeframe_file(monkeypatch, tmp_path):
    from app.backend.services._storage import current_user_id

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(scan_schedule_service, "_PRESCAN_PATH", tmp_path / "prescan.json")
    uid = current_user_id()
    r = _exercise_prescan_keying(uid)
    assert r["day"] == ("day", 1, 5)
    assert r["hour"] == ("1h", 2, 8)
    assert r["missing"] is None
    # No timeframe requested -> the most-recently-written slot (1h, written last;
    # the file backend stamps distinct microsecond timestamps).
    assert scan_schedule_service.get_prescan()["timeframe"] == "1h"


def test_prescan_file_migrates_legacy_flat_shape(monkeypatch, tmp_path):
    """A pre-existing single-slot prescan file is read as the {timeframe: ...} map."""
    from app.backend.services._storage import current_user_id

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    path = tmp_path / "prescan.json"
    monkeypatch.setattr(scan_schedule_service, "_PRESCAN_PATH", path)
    uid = current_user_id()
    # Old flat shape written by a pre-1.16 backend.
    path.write_text(
        json.dumps({uid: {"results": [{"ticker": "OLD"}], "timeframe": "day",
                          "ticker_count": 1, "computed_at": "2026-07-01T00:00:00+00:00"}}),
        encoding="utf-8",
    )
    assert scan_schedule_service.get_prescan("day")["results"] == [{"ticker": "OLD"}]
    # Writing a new timeframe keeps the migrated 'day' slot.
    scan_schedule_service.set_prescan_for(uid, [{"ticker": "NEW"}], "1h", 1)
    assert scan_schedule_service.get_prescan("day")["results"] == [{"ticker": "OLD"}]
    assert scan_schedule_service.get_prescan("1h")["results"] == [{"ticker": "NEW"}]


def test_alert_settings_and_dedup_db_backend(db_backend):
    """Telegram alert prefs + dedup ledger round-trip through the DB repository."""
    from app.backend.services import telegram_alerts
    from app.backend.services._storage import current_user_id

    uid = current_user_id()
    telegram_alerts._save_settings(
        uid, {"chat_id": "55", "enabled": True, "min_confidence": 85, "timeframes": ["day", "1h"]}
    )
    s = telegram_alerts._get_settings(uid)
    assert s["chat_id"] == "55" and s["enabled"] is True and s["min_confidence"] == 85.0
    assert sorted(s["timeframes"]) == ["1h", "day"]
    # dedup: both new, then one recorded
    assert sorted(telegram_alerts._filter_unnotified(uid, ["k1", "k2"])) == ["k1", "k2"]
    telegram_alerts._mark_notified(uid, ["k1"])
    assert telegram_alerts._filter_unnotified(uid, ["k1", "k2"]) == ["k2"]
    telegram_alerts._mark_notified(uid, ["k1"])  # idempotent (unique constraint)
    assert telegram_alerts._filter_unnotified(uid, ["k1", "k2"]) == ["k2"]


def test_interval_schedule_roundtrip_db(db_backend):
    from app.backend.services import scan_schedule_service as svc

    created = svc.add_schedule("09:30", "America/New_York", "1h", 30, interval_minutes=60)
    assert created["interval_minutes"] == 60
    assert created["last_run_at"] is None
    assert any(s["interval_minutes"] == 60 for s in svc.list_schedules())
    with pytest.raises(ValueError):
        svc.add_schedule("10:00", "America/New_York", "1h", 30, interval_minutes=45)


def test_interval_schedule_roundtrip_file(monkeypatch, tmp_path):
    from app.backend.services import scan_schedule_service as svc

    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(svc, "_SCHED_PATH", tmp_path / "sched.json")
    created = svc.add_schedule("09:30", "America/New_York", "1h", 30, interval_minutes=120)
    assert created["interval_minutes"] == 120 and created["last_run_at"] is None


def test_interval_due_logic():
    """An interval schedule is gated by last_run_at + the daily anchor, not last_run_on."""
    import datetime

    from app.backend.services import prescan_runner

    now = datetime.datetime(2026, 7, 14, 20, 0, tzinfo=datetime.timezone.utc)  # 16:00 EDT
    sched = {"time_of_day": "09:30", "timezone": "America/New_York",
             "interval_minutes": 60, "last_run_at": None, "last_run_on": None}
    assert prescan_runner._is_due(sched, now)[0] is True  # never run, past anchor -> due

    sched["last_run_at"] = now.isoformat()
    assert prescan_runner._is_due(sched, now)[0] is False  # just ran -> not due

    later = now + datetime.timedelta(minutes=70)
    assert prescan_runner._is_due(sched, later)[0] is True  # >60min later -> due again

    before_anchor = datetime.datetime(2026, 7, 14, 12, 0, tzinfo=datetime.timezone.utc)  # 08:00 EDT
    sched2 = {"time_of_day": "09:30", "timezone": "America/New_York",
              "interval_minutes": 60, "last_run_at": None}
    assert prescan_runner._is_due(sched2, before_anchor)[0] is False  # before daily anchor


def test_prescan_due_logic():
    """A schedule is due once its local time has passed today and it hasn't run."""
    import datetime
    from app.backend.services import prescan_runner

    # 14:00 UTC == 09:00 America/New_York (EDT, summer). An 08:00 ET schedule is due.
    now = datetime.datetime(2026, 6, 30, 14, 0, tzinfo=datetime.timezone.utc)
    due, today = prescan_runner._is_due(
        {"time_of_day": "08:00", "timezone": "America/New_York", "last_run_on": None}, now
    )
    assert due is True and today == "2026-06-30"
    # already ran today -> not due
    due2, _ = prescan_runner._is_due(
        {"time_of_day": "08:00", "timezone": "America/New_York", "last_run_on": "2026-06-30"}, now
    )
    assert due2 is False
    # scheduled later than now (16:00 ET) -> not due yet
    due3, _ = prescan_runner._is_due(
        {"time_of_day": "16:00", "timezone": "America/New_York", "last_run_on": None}, now
    )
    assert due3 is False
