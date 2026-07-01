"""SnapTrade orchestration + position normalization (the reused logic for the
positions view and the Phase B portfolio sync).
"""
from __future__ import annotations

import pytest

from app.backend.services import snaptrade_service as svc


def test_underlying_of_stock_nested_symbol():
    pos = {"symbol": {"symbol": {"symbol": "nvda"}}, "units": 10}
    assert svc.underlying_of(pos) == "NVDA"


def test_underlying_of_option_uses_underlying_symbol():
    pos = {
        "symbol": {
            "option_symbol": {
                "ticker": "NVDA 240119C00500000",
                "underlying_symbol": {"symbol": "NVDA"},
                "option_type": "CALL",
            }
        },
        "units": 2,
    }
    assert svc.underlying_of(pos) == "NVDA"


def test_normalize_stock_position_extracts_name():
    pos = {"symbol": {"symbol": {"symbol": "NVDA", "description": "NVIDIA CORP"}}, "units": 5, "price": 100.0}
    out = svc.normalize_stock_position(pos)
    assert out["symbol"] == "NVDA"
    assert out["name"] == "NVIDIA CORP"


def test_normalize_stock_position_computes_market_value():
    pos = {"symbol": {"symbol": {"symbol": "AAPL"}}, "units": 3, "price": 100.0}
    out = svc.normalize_stock_position(pos)
    assert out["symbol"] == "AAPL"
    assert out["underlying"] == "AAPL"
    assert out["units"] == 3.0
    assert out["market_value"] == 300.0
    assert out["kind"] == "stock"


def test_normalize_option_position_collapses_and_scales_by_contract():
    pos = {
        "symbol": {
            "option_symbol": {
                "ticker": "NVDA 240119C00500000",
                "underlying_symbol": {"symbol": "NVDA"},
                "option_type": "call",
                "strike_price": 500,
                "expiration_date": "2024-01-19",
            }
        },
        "units": 2,
        "price": 3.5,
        "average_purchase_price": 300.0,  # per CONTRACT (total premium), not per share
    }
    out = svc.normalize_option_position(pos)
    assert out["kind"] == "option"
    assert out["underlying"] == "NVDA"
    assert out["option_type"] == "CALL"
    assert out["strike"] == 500.0
    assert out["market_value"] == pytest.approx(2 * 3.5 * 100)
    # avg cost converted to per-share (300 / 100 = 3.0); cost basis uses ×100 like value
    assert out["avg_cost"] == pytest.approx(3.0)
    assert out["cost_basis"] == pytest.approx(2 * 3.0 * 100)


def test_normalize_option_per_share_avg_cost_not_divided():
    # A broker that reports avg cost PER SHARE (~ the price magnitude) must NOT be
    # divided by 100 — the heuristic keeps it as-is.
    pos = {
        "symbol": {"option_symbol": {"underlying_symbol": {"symbol": "SPCM"}, "option_type": "call"}},
        "units": 1,
        "price": 5.0,
        "average_purchase_price": 4.1,  # per share, close to price -> keep
    }
    out = svc.normalize_option_position(pos)
    assert out["avg_cost"] == pytest.approx(4.1)
    assert out["cost_basis"] == pytest.approx(1 * 4.1 * 100)  # 410, gain = 500 - 410


def test_connect_url_registers_once_then_reuses(monkeypatch):
    registered: list[str] = []
    saved: list[tuple[str, str]] = []
    state = {"creds": None}

    monkeypatch.setattr(svc, "current_user_id", lambda: "user-abc")
    monkeypatch.setattr(svc.store, "get_credentials", lambda: state["creds"])

    def _register(uid):
        registered.append(uid)
        return "new-secret"

    def _save(uid, secret):
        saved.append((uid, secret))
        state["creds"] = (uid, secret)
        return {}

    monkeypatch.setattr(svc.client, "register_user", _register)
    monkeypatch.setattr(svc.store, "save", _save)
    monkeypatch.setattr(
        svc.client, "login_portal_url", lambda uid, secret, custom_redirect=None: f"url:{uid}:{secret}"
    )

    first = svc.connect_url()
    assert first == "url:user-abc:new-secret"
    assert registered == ["user-abc"]
    assert saved == [("user-abc", "new-secret")]

    # second call: creds now present, must NOT re-register
    second = svc.connect_url()
    assert second == "url:user-abc:new-secret"
    assert registered == ["user-abc"]  # unchanged


def test_fetch_portfolio_raises_lookup_when_not_connected(monkeypatch):
    monkeypatch.setattr(svc.store, "get_credentials", lambda: None)
    with pytest.raises(LookupError):
        svc.fetch_portfolio()


def test_fetch_portfolio_normalizes_accounts(monkeypatch):
    monkeypatch.setattr(svc.store, "get_credentials", lambda: ("u1", "s1"))
    monkeypatch.setattr(
        svc.client, "list_accounts", lambda u, s: [{"id": "acc1", "name": "Roth", "number": "X1", "institution_name": "Fidelity"}]
    )
    monkeypatch.setattr(
        svc.client, "list_positions", lambda u, s, a: [{"symbol": {"symbol": {"symbol": "NVDA"}}, "units": 5, "price": 10.0}]
    )
    monkeypatch.setattr(
        svc.client,
        "list_option_holdings",
        lambda u, s, a: [{"symbol": {"option_symbol": {"underlying_symbol": {"symbol": "NVDA"}, "option_type": "PUT"}}, "units": 1, "price": 2.0}],
    )

    out = svc.fetch_portfolio()
    assert out["status"] == "ok"
    assert len(out["accounts"]) == 1
    acct = out["accounts"][0]
    assert acct["label"] == "Roth (X1)"
    assert acct["positions"][0]["symbol"] == "NVDA"
    assert acct["options"][0]["underlying"] == "NVDA"
    assert acct["options"][0]["option_type"] == "PUT"
