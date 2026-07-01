"""Tests for shared-data access requests + owner approval (Phase 3)."""
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
from app.backend.database.connection import Base, get_db
import app.backend.database.app_models  # noqa: F401
import app.backend.database.models  # noqa: F401
from app.backend.main import app
from app.backend.repositories.access_request_repository import AccessRequestRepository
from app.backend.services import _storage
from app.backend.services import key_resolver

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("AUTH_ENABLED", "OWNER_EMAIL", "OWNER_USER_ID", "SHARED_DATA_EMAILS", "CLERK_JWKS_URL"):
        monkeypatch.delenv(v, raising=False)
    auth_mod._jwks_clients.clear()
    yield


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(_storage, "SessionLocal", TestSession)
    monkeypatch.setenv("STORAGE_BACKEND", "db")

    def _override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestSession
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


@pytest.fixture(scope="module")
def rsa_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def auth_on(monkeypatch, rsa_keypair):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    pub = rsa_keypair.public_key()

    class _K:
        def __init__(self, k):
            self.key = k

    class _C:
        def get_signing_key_from_jwt(self, token):
            return _K(pub)

    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: _C())


def _bearer(pk, sub, email=None, verified=True):
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    payload = {"sub": sub, "exp": int(time.time()) + 3600}
    if email:
        payload["email"] = email
        payload["email_verified"] = verified
    return {"Authorization": f"Bearer {pyjwt.encode(payload, pem, algorithm='RS256')}"}


# ─── repository ──────────────────────────────────────────────────────────────


def test_repo_upsert_and_approve(db):
    s = db()
    try:
        repo = AccessRequestRepository(s)
        r = repo.upsert_for_user("user_x", "x@example.com")
        assert r.status == "pending"
        assert repo.is_email_approved("x@example.com") is False
        repo.set_status(r.id, "approved")
        assert repo.is_email_approved("X@Example.com") is True  # case-insensitive
    finally:
        s.close()


def test_repo_redenied_rerequest_resets_to_pending(db):
    s = db()
    try:
        repo = AccessRequestRepository(s)
        r = repo.upsert_for_user("user_x", "x@example.com")
        repo.set_status(r.id, "denied")
        again = repo.upsert_for_user("user_x", "x@example.com")
        assert again.status == "pending"
    finally:
        s.close()


# ─── resolver integration ────────────────────────────────────────────────────


def test_db_grant_makes_shared_approved(db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    s = db()
    try:
        repo = AccessRequestRepository(s)
        r = repo.upsert_for_user("user_x", "friend@example.com")
        repo.set_status(r.id, "approved")
    finally:
        s.close()
    assert key_resolver.is_shared_data_approved("user_x", "friend@example.com", True) is True
    # Pending (not approved) email is not shared-approved.
    assert key_resolver.is_shared_data_approved("user_y", "pending@example.com", True) is False


def test_is_owner(monkeypatch):
    monkeypatch.setenv("OWNER_USER_ID", "user_owner")
    assert key_resolver.is_owner("user_owner", None, False) is True
    assert key_resolver.is_owner("user_other", None, False) is False
    monkeypatch.delenv("OWNER_USER_ID")
    monkeypatch.setenv("OWNER_EMAIL", "boss@example.com")
    assert key_resolver.is_owner("u", "boss@example.com", True) is True
    assert key_resolver.is_owner("u", "boss@example.com", False) is False  # unverified


# ─── routes ──────────────────────────────────────────────────────────────────


def test_request_then_owner_approves(db, auth_on, rsa_keypair, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "boss@example.com")
    user = _bearer(rsa_keypair, "user_friend", "friend@example.com", verified=True)
    owner = _bearer(rsa_keypair, "user_boss", "boss@example.com", verified=True)

    # User requests access.
    assert client.post("/access/request", json={}, headers=user).status_code == 200
    me = client.get("/access/me", headers=user).json()
    assert me["request_status"] == "pending" and me["shared_data_approved"] is False

    # Non-owner can't list requests.
    assert client.get("/access/requests", headers=user).status_code == 403

    # Owner sees + approves.
    reqs = client.get("/access/requests", headers=owner).json()
    assert len(reqs) == 1 and reqs[0]["email"] == "friend@example.com"
    rid = reqs[0]["id"]
    assert client.post(f"/access/requests/{rid}/approve", headers=owner).status_code == 200

    # User is now shared-approved.
    me2 = client.get("/access/me", headers=user).json()
    assert me2["shared_data_approved"] is True


def test_owner_me_flags(db, auth_on, rsa_keypair, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "boss@example.com")
    owner = _bearer(rsa_keypair, "user_boss", "boss@example.com", verified=True)
    me = client.get("/access/me", headers=owner).json()
    assert me["is_owner"] is True and me["shared_data_approved"] is True


def test_requires_auth(db, auth_on):
    assert client.get("/access/me").status_code == 401


def test_owner_deny_deletes_request(db, auth_on, rsa_keypair, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "boss@example.com")
    user = _bearer(rsa_keypair, "user_friend", "friend@example.com", verified=True)
    owner = _bearer(rsa_keypair, "user_boss", "boss@example.com", verified=True)

    client.post("/access/request", json={}, headers=user)
    rid = client.get("/access/requests", headers=owner).json()[0]["id"]

    # Deny = delete: the row is gone, not kept as 'denied'.
    assert client.delete(f"/access/requests/{rid}", headers=owner).status_code == 200
    assert client.get("/access/requests", headers=owner).json() == []
    me = client.get("/access/me", headers=user).json()
    assert me["request_status"] is None and me["shared_data_approved"] is False


def test_owner_revoke_approved_user(db, auth_on, rsa_keypair, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "boss@example.com")
    user = _bearer(rsa_keypair, "user_friend", "friend@example.com", verified=True)
    owner = _bearer(rsa_keypair, "user_boss", "boss@example.com", verified=True)

    client.post("/access/request", json={}, headers=user)
    rid = client.get("/access/requests", headers=owner).json()[0]["id"]
    client.post(f"/access/requests/{rid}/approve", headers=owner)
    assert client.get("/access/me", headers=user).json()["shared_data_approved"] is True

    # Revoke = delete an approved row: the user loses shared access.
    assert client.delete(f"/access/requests/{rid}", headers=owner).status_code == 200
    assert client.get("/access/me", headers=user).json()["shared_data_approved"] is False


def test_delete_requires_owner(db, auth_on, rsa_keypair):
    user = _bearer(rsa_keypair, "user_friend", "friend@example.com", verified=True)
    assert client.delete("/access/requests/1", headers=user).status_code == 403


def test_list_excludes_default_user(db, auth_on, rsa_keypair, monkeypatch):
    monkeypatch.setenv("OWNER_EMAIL", "boss@example.com")
    owner = _bearer(rsa_keypair, "user_boss", "boss@example.com", verified=True)
    s = db()
    try:
        AccessRequestRepository(s).upsert_for_user("default", "owner@local")
    finally:
        s.close()
    assert client.get("/access/requests", headers=owner).json() == []
