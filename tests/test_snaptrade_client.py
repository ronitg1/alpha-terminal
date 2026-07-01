"""Tests for the signed SnapTrade client.

The signing test independently recomputes the expected signature (HMAC-SHA256 over
the compact key-sorted sig object) so it pins the algorithm rather than mirroring
the implementation. The request tests monkeypatch ``httpx.request`` and ``time``
so no network is touched and the timestamp is deterministic.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from app.backend.services import snaptrade_client as sc


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "client-123")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "consumer-secret")
    yield


def test_snaptrade_configured_reflects_env(monkeypatch):
    monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SNAPTRADE_CONSUMER_KEY", raising=False)
    assert sc.snaptrade_configured() is False
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "x")
    assert sc.snaptrade_configured() is False  # still missing consumer key
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "y")
    assert sc.snaptrade_configured() is True


def test_require_credentials_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SNAPTRADE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SNAPTRADE_CONSUMER_KEY", raising=False)
    with pytest.raises(sc.SnapTradeNotConfigured):
        sc._request("GET", "/api/v1/accounts")


def test_sign_matches_independent_hmac():
    consumer_key = "consumer-secret"
    path = "/api/v1/snapTrade/registerUser"
    query = "clientId=abc&timestamp=100"
    body = {"userId": "u1"}

    expected_content = json.dumps(
        {"content": body, "path": path, "query": query},
        separators=(",", ":"),
        sort_keys=True,
    )
    expected = base64.b64encode(
        hmac.new(consumer_key.encode(), expected_content.encode(), hashlib.sha256).digest()
    ).decode()

    assert sc._sign(consumer_key, path, query, body) == expected


def test_sign_is_null_content_for_get():
    consumer_key = "k"
    sig = sc._sign(consumer_key, "/api/v1/accounts", "clientId=abc&timestamp=1", None)
    expected_content = json.dumps(
        {"content": None, "path": "/api/v1/accounts", "query": "clientId=abc&timestamp=1"},
        separators=(",", ":"),
        sort_keys=True,
    )
    expected = base64.b64encode(
        hmac.new(consumer_key.encode(), expected_content.encode(), hashlib.sha256).digest()
    ).decode()
    assert sig == expected


def _capture_request(monkeypatch, response: httpx.Response):
    calls: list[dict] = []

    def _fake_request(method, url, *, headers, json, timeout, follow_redirects):
        calls.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        return response

    monkeypatch.setattr(sc.time, "time", lambda: 1000)
    monkeypatch.setattr(sc.httpx, "request", _fake_request)
    return calls


def test_request_signs_the_exact_url_query(monkeypatch, configured):
    resp = httpx.Response(200, json={"ok": True})
    calls = _capture_request(monkeypatch, resp)

    sc._request("GET", "/api/v1/accounts", query={"userId": "u1", "userSecret": "s1"})

    assert len(calls) == 1
    url = calls[0]["url"]
    # clientId + timestamp added; params sorted deterministically.
    query_string = url.split("?", 1)[1]
    assert "clientId=client-123" in query_string
    assert "timestamp=1000" in query_string
    assert "userId=u1" in query_string and "userSecret=s1" in query_string
    # The Signature header must sign the SAME query string that is on the URL.
    expected_sig = sc._sign("consumer-secret", "/api/v1/accounts", query_string, None)
    assert calls[0]["headers"]["Signature"] == expected_sig


def test_request_sends_body_as_signed_content(monkeypatch, configured):
    resp = httpx.Response(200, json={"userSecret": "abc"})
    calls = _capture_request(monkeypatch, resp)

    sc._request("POST", "/api/v1/snapTrade/registerUser", body={"userId": "u1"})

    assert calls[0]["json"] == {"userId": "u1"}
    query_string = calls[0]["url"].split("?", 1)[1]
    expected_sig = sc._sign(
        "consumer-secret", "/api/v1/snapTrade/registerUser", query_string, {"userId": "u1"}
    )
    assert calls[0]["headers"]["Signature"] == expected_sig


def test_request_maps_401_to_auth_required(monkeypatch, configured):
    _capture_request(monkeypatch, httpx.Response(403, json={"detail": "bad sig"}))
    with pytest.raises(sc.SnapTradeAuthRequired):
        sc._request("GET", "/api/v1/accounts")


def test_request_maps_other_4xx_to_error(monkeypatch, configured):
    _capture_request(monkeypatch, httpx.Response(400, json={"detail": "already registered"}))
    with pytest.raises(sc.SnapTradeError) as exc:
        sc._request("POST", "/api/v1/snapTrade/registerUser", body={"userId": "u1"})
    assert "already registered" in str(exc.value)


def test_register_user_returns_secret(monkeypatch, configured):
    _capture_request(monkeypatch, httpx.Response(200, json={"userId": "u1", "userSecret": "sek"}))
    assert sc.register_user("u1") == "sek"


def test_register_user_raises_without_secret(monkeypatch, configured):
    _capture_request(monkeypatch, httpx.Response(200, json={"userId": "u1"}))
    with pytest.raises(sc.SnapTradeError):
        sc.register_user("u1")


def test_login_portal_url_returns_redirect(monkeypatch, configured):
    _capture_request(monkeypatch, httpx.Response(200, json={"redirectURI": "https://app.snaptrade.com/x"}))
    assert sc.login_portal_url("u1", "s1") == "https://app.snaptrade.com/x"


def test_list_accounts_returns_list(monkeypatch, configured):
    _capture_request(monkeypatch, httpx.Response(200, json=[{"id": "a1"}, {"id": "a2"}]))
    assert sc.list_accounts("u1", "s1") == [{"id": "a1"}, {"id": "a2"}]


def test_list_option_holdings_unwraps_dict(monkeypatch, configured):
    _capture_request(
        monkeypatch, httpx.Response(200, json={"option_positions": [{"units": 1}]})
    )
    assert sc.list_option_holdings("u1", "s1", "a1") == [{"units": 1}]
