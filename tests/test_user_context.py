"""Tests for per-user data isolation (Phase 3, step 2).

Three things are proven:

1. **Auth off (default):** data routes work with no token and resolve to the
   single-tenant ``default`` user — unchanged behavior, safe to ship dormant.
2. **Auth on, full stack:** the ``UserContextMiddleware`` binds each request's
   Clerk ``sub`` to the context var, so two different tokens get two completely
   separate datasets through real CRUD routes; an unauthenticated request to a
   gated route is 401'd.
3. **Context propagation (the risky bit):** the bound user survives into an SSE
   ``StreamingResponse`` body *and* into an ``asyncio.to_thread`` worker — the
   two places the morning-scan route relies on.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from app.backend import auth as auth_mod
from app.backend.context import (
    DEFAULT_USER_ID,
    current_user_id,
    reset_current_user_id,
    set_current_user_id,
)
from app.backend.database.connection import Base
import app.backend.database.app_models  # noqa: F401  (register tables on Base)
from app.backend.main import app
from app.backend.middleware import UserContextMiddleware
from app.backend.repositories.scan_repository import ScanRepository
from app.backend.routes import sleeves as sleeves_routes
from app.backend.services import _storage

client = TestClient(app)


# ─── fixtures / helpers ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    auth_mod._jwks_clients.clear()
    yield
    auth_mod._jwks_clients.clear()


@pytest.fixture(scope="module")
def rsa_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def auth_on(monkeypatch, rsa_keypair):
    """Auth enabled, with the verifier's JWKS pointed at our in-memory key."""
    monkeypatch.setenv("AUTH_ENABLED", "1")
    public_key = rsa_keypair.public_key()

    class _FakeSigningKey:
        def __init__(self, key):
            self.key = key

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey(public_key)

    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: _FakeJWKSClient())


@pytest.fixture()
def db_backend(monkeypatch):
    """DB storage on an isolated in-memory SQLite engine (mirrors the cutover
    tests), so service calls hit Postgres-shaped repositories."""
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
        yield TestSession
    finally:
        engine.dispose()


def _token(private_key, *, sub):
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    payload = {"sub": sub, "exp": int(time.time()) + 3600}
    return pyjwt.encode(payload, priv_pem, algorithm="RS256")


def _bearer(private_key, sub):
    return {"Authorization": f"Bearer {_token(private_key, sub=sub)}"}


# ─── auth OFF — unchanged single-tenant behavior ─────────────────────────────


def test_data_route_open_when_auth_off(db_backend):
    # No token, auth off: the gated router still serves, scoped to `default`.
    resp = client.get("/sleeves/watchlists")
    assert resp.status_code == 200
    assert resp.json() == {"watchlists": []}


# ─── auth ON — enforcement + isolation ───────────────────────────────────────


def test_gated_route_401_without_token(auth_on, db_backend):
    resp = client.get("/sleeves/watchlists")
    assert resp.status_code == 401


def test_two_tokens_get_separate_datasets(auth_on, db_backend, rsa_keypair):
    a = _bearer(rsa_keypair, "user_alice")
    b = _bearer(rsa_keypair, "user_bob")

    # Alice creates a watchlist; Bob creates a different one.
    r = client.post("/sleeves/watchlists", json={"name": "Tech", "tickers": [{"ticker": "NVDA"}]}, headers=a)
    assert r.status_code == 200
    r = client.post("/sleeves/watchlists", json={"name": "Energy", "tickers": [{"ticker": "XOM"}]}, headers=b)
    assert r.status_code == 200

    # Each sees ONLY their own.
    alice_lists = client.get("/sleeves/watchlists", headers=a).json()["watchlists"]
    bob_lists = client.get("/sleeves/watchlists", headers=b).json()["watchlists"]
    assert [w["name"] for w in alice_lists] == ["Tech"]
    assert [w["name"] for w in bob_lists] == ["Energy"]


def test_unauthenticated_sees_no_data_not_owner_data(auth_on, db_backend, rsa_keypair):
    # Owner seeds data under the default user via the file/db path is moot here;
    # instead prove a *bad token* never reads another user's rows. Alice writes,
    # then a request with a garbage token is rejected (401) — never Alice's data.
    a = _bearer(rsa_keypair, "user_alice")
    client.post("/sleeves/watchlists", json={"name": "Tech", "tickers": []}, headers=a)

    bad = client.get("/sleeves/watchlists", headers={"Authorization": "Bearer junk.token"})
    assert bad.status_code == 401


# ─── context propagation into StreamingResponse + asyncio.to_thread ──────────


def _propagation_app() -> Starlette:
    """A throwaway app whose one route reads current_user_id() inside an SSE
    generator body AND inside an asyncio.to_thread worker."""

    async def stream(request):
        async def gen():
            yield f"gen={current_user_id()};".encode()
            in_thread = await asyncio.to_thread(current_user_id)
            yield f"thread={in_thread}".encode()

        return StreamingResponse(gen(), media_type="text/plain")

    sub_app = Starlette(routes=[Route("/s", stream)])
    sub_app.add_middleware(UserContextMiddleware)
    return sub_app


def test_user_propagates_into_stream_and_thread_when_auth_on(auth_on, rsa_keypair):
    sub_client = TestClient(_propagation_app())
    resp = sub_client.get("/s", headers=_bearer(rsa_keypair, "user_stream"))
    assert resp.status_code == 200
    assert resp.text == "gen=user_stream;thread=user_stream"


def test_user_propagation_defaults_when_auth_off():
    sub_client = TestClient(_propagation_app())
    resp = sub_client.get("/s")
    assert resp.status_code == 200
    assert resp.text == f"gen={DEFAULT_USER_ID};thread={DEFAULT_USER_ID}"


def test_concurrent_requests_do_not_bleed(auth_on, rsa_keypair):
    """Two requests in flight at once, each with a different token, must each
    observe only their own user — proving the context var is per-request even
    under interleaving (the to_thread await yields, allowing overlap)."""
    sub_app = _propagation_app()

    async def _run():
        transport = httpx.ASGITransport(app=sub_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await asyncio.gather(
                ac.get("/s", headers=_bearer(rsa_keypair, "user_a")),
                ac.get("/s", headers=_bearer(rsa_keypair, "user_b")),
            )

    ra, rb = asyncio.run(_run())
    assert ra.text == "gen=user_a;thread=user_a"
    assert rb.text == "gen=user_b;thread=user_b"


# ─── scan persistence is scoped to the current user ──────────────────────────


def test_scan_persistence_scoped_to_current_user(db_backend):
    """The morning-scan write path (_write_scan_json_ui -> ScanRepository) must
    store under the request's user, and another user must not see it."""
    token = set_current_user_id("user_scan_owner")
    try:
        sleeves_routes._write_scan_json_ui(
            [{"ticker": "NVDA", "weighted_score": 1.0}], Path("ignored"), "2026-06-27"
        )
    finally:
        reset_current_user_id(token)

    with db_backend() as db:
        owned = ScanRepository(db, "user_scan_owner").get("2026-06-27")
        other = ScanRepository(db, "someone_else").get("2026-06-27")

    assert owned is not None
    assert owned["row_count"] == 1
    assert other is None
