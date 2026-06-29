"""Tests for first-login provisioning + owner data-claim (Phase 3, step 5)."""
from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend import auth as auth_mod
from app.backend.database.app_models import Portfolio, User, UserSettings, Watchlist
from app.backend.database.connection import Base
import app.backend.database.app_models  # noqa: F401
import app.backend.database.models  # noqa: F401
from app.backend.main import app
from app.backend.services import _storage
from app.backend.services import provisioning

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    provisioning._provisioned.clear()
    auth_mod._jwks_clients.clear()
    yield
    provisioning._provisioned.clear()


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(_storage, "SessionLocal", TestSession)
    monkeypatch.setenv("STORAGE_BACKEND", "db")
    yield TestSession
    engine.dispose()


@pytest.fixture(scope="module")
def rsa_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def auth_on(monkeypatch, rsa_keypair):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    public_key = rsa_keypair.public_key()

    class _K:
        def __init__(self, k):
            self.key = k

    class _C:
        def get_signing_key_from_jwt(self, token):
            return _K(public_key)

    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: _C())


def _bearer(private_key, sub, email=None, email_verified=False):
    pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    payload = {"sub": sub, "exp": int(time.time()) + 3600}
    if email:
        payload["email"] = email
        payload["email_verified"] = email_verified
    return {"Authorization": f"Bearer {pyjwt.encode(payload, pem, algorithm='RS256')}"}


def _seed_default_portfolio(db):
    with db() as s:
        s.add(Portfolio(user_id="default", name="mega", allocation_pct=100.0,
                        agents=["alpha_seeker"], agent_weights={"alpha_seeker": 1.0}, tickers=["NVDA"]))
        s.add(Watchlist(user_id="default", name="wl", entries=[]))
        s.commit()


# ─── unit: ensure_provisioned ────────────────────────────────────────────────


def test_new_user_gets_starter(db):
    provisioning.ensure_provisioned("user_new", "someone@example.com")
    with db() as s:
        assert s.get(User, "user_new") is not None
        assert [p.name for p in s.query(Portfolio).filter_by(user_id="user_new")] == ["starter"]
        assert s.query(UserSettings).filter_by(user_id="user_new").first() is not None


def test_owner_verified_email_claims_default_data(db, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    _seed_default_portfolio(db)
    provisioning.ensure_provisioned("user_owner", "owner@example.com", email_verified=True)
    with db() as s:
        assert [p.name for p in s.query(Portfolio).filter_by(user_id="user_owner")] == ["mega"]
        assert s.query(Portfolio).filter_by(user_id="default").count() == 0
        assert s.query(Watchlist).filter_by(user_id="user_owner").count() == 1


def test_owner_UNVERIFIED_email_does_not_claim(db, monkeypatch):
    # SECURITY: an unverified email matching OWNER_EMAIL must NOT claim — this is
    # the attacker-spoofs-the-owner's-email-on-open-signup defense.
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    _seed_default_portfolio(db)
    provisioning.ensure_provisioned("attacker", "owner@example.com", email_verified=False)
    with db() as s:
        assert s.query(Portfolio).filter_by(user_id="default").count() == 1  # untouched
        assert [p.name for p in s.query(Portfolio).filter_by(user_id="attacker")] == ["starter"]


def test_owner_user_id_claims_without_email(db, monkeypatch):
    # Unspoofable claim path: match on the Clerk sub.
    monkeypatch.setenv("OWNER_USER_ID", "user_owner_sub")
    _seed_default_portfolio(db)
    provisioning.ensure_provisioned("user_owner_sub", None, email_verified=False)
    with db() as s:
        assert [p.name for p in s.query(Portfolio).filter_by(user_id="user_owner_sub")] == ["mega"]
        assert s.query(Portfolio).filter_by(user_id="default").count() == 0


def test_non_owner_does_not_claim(db, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    _seed_default_portfolio(db)
    provisioning.ensure_provisioned("user_other", "someone@example.com")
    with db() as s:
        # default data untouched; new user got a starter instead.
        assert s.query(Portfolio).filter_by(user_id="default").count() == 1
        assert [p.name for p in s.query(Portfolio).filter_by(user_id="user_other")] == ["starter"]


def test_provisioning_idempotent(db):
    provisioning.ensure_provisioned("user_x", "x@example.com")
    provisioning._provisioned.clear()  # force the DB existence check to run again
    provisioning.ensure_provisioned("user_x", "x@example.com")
    with db() as s:
        assert s.query(Portfolio).filter_by(user_id="user_x").count() == 1  # no duplicate


def test_default_user_never_provisioned(db):
    provisioning.ensure_provisioned("default", None)
    with db() as s:
        assert s.query(Portfolio).filter_by(user_id="default").count() == 0


# ─── end-to-end: a gated request triggers the claim ──────────────────────────


def test_owner_claims_on_first_request(db, auth_on, rsa_keypair, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    _seed_default_portfolio(db)

    # Any gated route runs get_current_user_id -> ensure_provisioned.
    resp = client.get(
        "/sleeves/watchlists",
        headers=_bearer(rsa_keypair, "user_owner", "owner@example.com", email_verified=True),
    )
    assert resp.status_code == 200
    with db() as s:
        assert s.get(User, "user_owner") is not None
        assert s.query(Portfolio).filter_by(user_id="user_owner").count() == 1
        assert s.query(Portfolio).filter_by(user_id="default").count() == 0


def test_auth_off_does_not_provision(db):
    # Auth off: no provisioning, no users created.
    client.get("/sleeves/watchlists")
    with db() as s:
        assert s.query(User).count() == 0
