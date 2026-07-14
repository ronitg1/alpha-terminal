"""SnapTrade route gating and status shape (auth off — single-tenant local).

With ``AUTH_ENABLED`` unset the approval gate is skipped (the local owner), so
these tests exercise the *configured* gate and the error mapping. The approval
gate under auth-on is covered by the key-resolver tests for
``is_shared_data_approved``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.backend.main import app
from app.backend.routes import snaptrade as route

client = TestClient(app)


def test_status_reports_unconfigured(monkeypatch):
    monkeypatch.setattr(route, "snaptrade_configured", lambda: False)
    monkeypatch.setattr(route.snaptrade_service, "connection_status", lambda: {"configured": False, "connected": False, "connection": None})

    resp = client.get("/snaptrade/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["connected"] is False
    assert body["approved"] is True  # auth off => local owner allowed


def test_connect_blocked_when_unconfigured(monkeypatch):
    monkeypatch.setattr(route, "snaptrade_configured", lambda: False)

    resp = client.post("/snaptrade/connect", json={})

    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


def test_connect_returns_redirect_when_configured(monkeypatch):
    monkeypatch.setattr(route, "snaptrade_configured", lambda: True)
    monkeypatch.setattr(route.snaptrade_service, "connect_url", lambda custom_redirect=None: "https://app.snaptrade.com/portal")

    resp = client.post("/snaptrade/connect", json={})

    assert resp.status_code == 200
    assert resp.json()["redirect_uri"] == "https://app.snaptrade.com/portal"


def test_portfolio_maps_not_connected_to_400(monkeypatch):
    monkeypatch.setattr(route, "snaptrade_configured", lambda: True)

    def _raise():
        raise LookupError("no connection")

    monkeypatch.setattr(route.snaptrade_service, "fetch_portfolio", _raise)

    resp = client.get("/snaptrade/portfolio")

    assert resp.status_code == 400


def test_portfolio_success(monkeypatch):
    monkeypatch.setattr(route, "snaptrade_configured", lambda: True)
    monkeypatch.setattr(
        route.snaptrade_service,
        "fetch_portfolio",
        lambda: {"status": "ok", "accounts": [{"id": "a1", "label": "Roth (X1)", "positions": [], "options": []}]},
    )

    resp = client.get("/snaptrade/portfolio")

    assert resp.status_code == 200
    assert resp.json()["accounts"][0]["label"] == "Roth (X1)"


def test_disconnect(monkeypatch):
    monkeypatch.setattr(route, "snaptrade_configured", lambda: True)
    monkeypatch.setattr(route.snaptrade_service, "disconnect", lambda: True)

    resp = client.delete("/snaptrade/connection")

    assert resp.status_code == 200
    assert resp.json()["disconnected"] is True


def test_portfolio_upstream_error_sanitized(monkeypatch):
    from app.backend.services.snaptrade_client import SnapTradeError

    monkeypatch.setattr(route, "snaptrade_configured", lambda: True)

    def _raise():
        raise SnapTradeError("raw upstream account detail")

    monkeypatch.setattr(route.snaptrade_service, "fetch_portfolio", _raise)

    resp = client.get("/snaptrade/portfolio")

    assert resp.status_code == 502
    assert "raw upstream account detail" not in resp.json()["detail"]
