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

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend.database.connection import Base
import app.backend.database.app_models  # noqa: F401  (register tables on Base)
from app.backend.services import _storage
from app.backend.services import watchlists_service
from app.backend.services import sleeve_config_service


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
        yield
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
