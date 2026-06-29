"""Tests for BYOK key storage (Phase 3, step 3): encryption at rest, user-scoped
repository, and the per-user /api-keys routes (validate-on-save, never return the
key value, full isolation between users).
"""
from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend import auth as auth_mod
from app.backend import crypto
from app.backend.database.connection import Base, get_db
import app.backend.database.app_models  # noqa: F401  (register tables)
import app.backend.database.models  # noqa: F401  (register api_keys table)
from app.backend.database.models import ApiKey
from app.backend.main import app
from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.routes import api_keys as api_keys_route

client = TestClient(app)


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _enc_key(monkeypatch):
    """A throwaway Fernet key + clean auth env for every test."""
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    auth_mod._jwks_clients.clear()
    yield
    auth_mod._jwks_clients.clear()


@pytest.fixture()
def db_session(monkeypatch):
    """In-memory DB with get_db overridden to use it; validation stubbed to pass."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    # Don't hit the network when saving keys.
    monkeypatch.setattr(api_keys_route, "validate_provider_key", lambda provider, key: None)
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
    public_key = rsa_keypair.public_key()

    class _FakeSigningKey:
        def __init__(self, key):
            self.key = key

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey(public_key)

    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: _FakeJWKSClient())


def _bearer(private_key, sub):
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    token = pyjwt.encode({"sub": sub, "exp": int(time.time()) + 3600}, priv_pem, algorithm="RS256")
    return {"Authorization": f"Bearer {token}"}


# ─── crypto ──────────────────────────────────────────────────────────────────


def test_encrypt_roundtrip_and_not_plaintext():
    token = crypto.encrypt("sk-secret-123")
    assert token != "sk-secret-123"
    assert crypto.decrypt(token) == "sk-secret-123"


def test_encrypt_raises_when_no_key(monkeypatch):
    monkeypatch.delenv("API_KEY_ENCRYPTION_KEY", raising=False)
    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.encrypt("x")


def test_decrypt_supports_key_rotation(monkeypatch):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", old)
    token = crypto.encrypt("rotate-me")
    # New key first, old kept for decrypt — the MultiFernet should still read it.
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", f"{new},{old}")
    assert crypto.decrypt(token) == "rotate-me"


def test_decrypt_fails_after_key_removed(monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    token = crypto.encrypt("gone")
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())  # unrelated key
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token)


# ─── repository (encrypted at rest + user-scoped) ────────────────────────────


def test_repo_encrypts_at_rest_and_isolates_users(db_session):
    db = db_session()
    try:
        ApiKeyRepository(db, "user_a").set_key("deepseek", "key-A")
        ApiKeyRepository(db, "user_b").set_key("deepseek", "key-B")

        # Stored ciphertext is not the plaintext.
        rows = db.query(ApiKey).all()
        assert all(r.key_value not in ("key-A", "key-B") for r in rows)

        # Each user reads only their own decrypted key.
        assert ApiKeyRepository(db, "user_a").get_decrypted("deepseek") == "key-A"
        assert ApiKeyRepository(db, "user_b").get_decrypted("deepseek") == "key-B"

        # A user sees none of the other's keys.
        a_providers = [r.provider for r in ApiKeyRepository(db, "user_a").list_keys()]
        assert a_providers == ["deepseek"]
        assert db.query(ApiKey).filter(ApiKey.user_id == "user_a").count() == 1
    finally:
        db.close()


def test_repo_upsert_replaces_and_delete(db_session):
    db = db_session()
    try:
        repo = ApiKeyRepository(db, "user_a")
        repo.set_key("finnhub", "first")
        repo.set_key("finnhub", "second")  # replace, not duplicate
        assert repo.get_decrypted("finnhub") == "second"
        assert db.query(ApiKey).filter(ApiKey.user_id == "user_a").count() == 1

        assert repo.delete("finnhub") is True
        assert repo.get_decrypted("finnhub") is None
        assert repo.delete("finnhub") is False
    finally:
        db.close()


# ─── routes ──────────────────────────────────────────────────────────────────


def test_route_upsert_rejects_unknown_provider(db_session):
    resp = client.post("/api-keys/", json={"provider": "openai", "key_value": "x"})
    assert resp.status_code == 400


def test_route_upsert_validates_key(db_session, monkeypatch):
    from app.backend.services.api_key_validation import KeyValidationError

    def _reject(provider, key):
        raise KeyValidationError("nope")

    monkeypatch.setattr(api_keys_route, "validate_provider_key", _reject)
    resp = client.post("/api-keys/", json={"provider": "deepseek", "key_value": "bad"})
    assert resp.status_code == 400
    assert "nope" in resp.json()["detail"]


def test_route_never_returns_key_value(db_session):
    resp = client.post("/api-keys/", json={"provider": "deepseek", "key_value": "sk-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert "key_value" not in body
    assert body["provider"] == "deepseek"
    assert body["has_key"] is True

    listed = client.get("/api-keys/").json()
    assert all("key_value" not in row for row in listed)


def test_route_stores_encrypted(db_session):
    client.post("/api-keys/", json={"provider": "massive", "key_value": "poly-123"})
    db = db_session()
    try:
        row = db.query(ApiKey).filter(ApiKey.provider == "massive").first()
        assert row is not None
        assert row.key_value != "poly-123"
        assert crypto.decrypt(row.key_value) == "poly-123"
    finally:
        db.close()


def test_route_auth_off_uses_default_user(db_session):
    client.post("/api-keys/", json={"provider": "deepseek", "key_value": "k"})
    db = db_session()
    try:
        row = db.query(ApiKey).first()
        assert row.user_id == "default"
    finally:
        db.close()


def test_route_isolation_between_users(db_session, auth_on, rsa_keypair):
    a = _bearer(rsa_keypair, "user_alice")
    b = _bearer(rsa_keypair, "user_bob")

    client.post("/api-keys/", json={"provider": "deepseek", "key_value": "alice-key"}, headers=a)
    client.post("/api-keys/", json={"provider": "finnhub", "key_value": "bob-key"}, headers=b)

    alice = client.get("/api-keys/", headers=a).json()
    bob = client.get("/api-keys/", headers=b).json()
    assert [r["provider"] for r in alice] == ["deepseek"]
    assert [r["provider"] for r in bob] == ["finnhub"]

    # Alice cannot fetch Bob's provider.
    assert client.get("/api-keys/finnhub", headers=a).status_code == 404
    assert client.get("/api-keys/finnhub", headers=b).status_code == 200


def test_route_requires_auth_when_enabled(db_session, auth_on):
    assert client.get("/api-keys/").status_code == 401


def test_route_provider_unavailable_returns_503(db_session, monkeypatch):
    from app.backend.services.api_key_validation import KeyValidationUnavailable

    def _down(provider, key):
        raise KeyValidationUnavailable("provider down")

    monkeypatch.setattr(api_keys_route, "validate_provider_key", _down)
    resp = client.post("/api-keys/", json={"provider": "deepseek", "key_value": "k"})
    assert resp.status_code == 503  # NOT 400 — don't blame the user's key


def test_route_rejects_oversized_key(db_session):
    resp = client.post("/api-keys/", json={"provider": "deepseek", "key_value": "x" * 513})
    assert resp.status_code == 422  # schema max_length


# ─── validate_provider_key status classification ─────────────────────────────


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.mark.parametrize(
    "status, expect",
    [(200, None), (401, "bad"), (403, "bad"), (400, "bad"), (429, "unavail"), (500, "unavail"), (503, "unavail")],
)
def test_validation_classifies_status(monkeypatch, status, expect):
    from app.backend.services import api_key_validation as v

    monkeypatch.setattr(v.httpx, "get", lambda *a, **k: _FakeResp(status))
    if expect is None:
        v.validate_provider_key("deepseek", "k")  # no raise
    elif expect == "bad":
        with pytest.raises(v.KeyValidationError):
            v.validate_provider_key("deepseek", "k")
    else:
        with pytest.raises(v.KeyValidationUnavailable):
            v.validate_provider_key("deepseek", "k")


def test_validation_network_error_is_unavailable(monkeypatch):
    import httpx as _httpx

    from app.backend.services import api_key_validation as v

    def _boom(*a, **k):
        raise _httpx.ConnectError("dns")

    monkeypatch.setattr(v.httpx, "get", _boom)
    with pytest.raises(v.KeyValidationUnavailable):
        v.validate_provider_key("massive", "k")


# ─── startup guard: auth on requires encryption configured ───────────────────


def test_startup_guard_blocks_when_auth_on_without_encryption(monkeypatch):
    from app.backend.main import _check_auth_encryption

    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.delenv("API_KEY_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError):
        _check_auth_encryption()


def test_startup_guard_ok_when_auth_on_with_encryption(monkeypatch):
    from app.backend.main import _check_auth_encryption

    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _check_auth_encryption()  # no raise


def test_startup_guard_noop_when_auth_off(monkeypatch):
    from app.backend.main import _check_auth_encryption

    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("API_KEY_ENCRYPTION_KEY", raising=False)
    _check_auth_encryption()  # no raise — dormant
