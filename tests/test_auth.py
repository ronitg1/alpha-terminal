"""Tests for the Clerk-auth seam (``app/backend/auth.py``) and the ``/auth/me``
route, exercised through both flag states.

Two things are proven here:

1. **Auth off (default):** the dependency is dormant — every request resolves to
   :data:`DEFAULT_USER_ID` regardless of headers, so local + current cloud are
   unchanged and the flag is safe to ship.
2. **Auth on:** a request needs a valid Clerk session JWT. We verify the *real*
   RS256 signature path by generating an RSA keypair, signing tokens with the
   private key, and pointing the verifier's JWKS client at the matching public
   key. Missing/garbage/expired/wrong-issuer/sub-less tokens all 401; a server
   with no JWKS configured 500s.
"""
from __future__ import annotations

import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.backend import auth as auth_mod
from app.backend.database.app_models import DEFAULT_USER_ID
from app.backend.main import app

client = TestClient(app)


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch):
    """Each test starts from a known-clean auth config: flag off, no Clerk env,
    empty JWKS-client cache (it's module-level and would otherwise leak)."""
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    auth_mod._jwks_clients.clear()
    yield
    auth_mod._jwks_clients.clear()


@pytest.fixture(scope="module")
def rsa_keypair():
    """One RSA keypair for the whole module — generation is the slow part."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def use_fake_jwks(monkeypatch, rsa_keypair):
    """Point the verifier's JWKS lookup at our in-memory public key, so the real
    RS256 verification runs but no network call is made."""
    public_key = rsa_keypair.public_key()

    class _FakeSigningKey:
        def __init__(self, key):
            self.key = key

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):  # noqa: D401 - test stub
            return _FakeSigningKey(public_key)

    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: _FakeJWKSClient())


def _make_token(private_key, *, sub="user_abc123", exp_delta=3600, iss=None, drop_sub=False, kid=None):
    """Sign a Clerk-style RS256 session token with the test private key."""
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    payload: dict = {"exp": int(time.time()) + exp_delta}
    if not drop_sub:
        payload["sub"] = sub
    if iss is not None:
        payload["iss"] = iss
    headers = {"kid": kid} if kid else None
    return pyjwt.encode(payload, priv_pem, algorithm="RS256", headers=headers)


def _build_jwks(public_key, kid):
    """A JWKS document (as Clerk publishes) holding the test public key."""
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


# ─── auth OFF (default, dormant) ─────────────────────────────────────────────


def test_auth_off_returns_default_without_token():
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == DEFAULT_USER_ID
    assert body["auth_enabled"] is False
    # /auth/me also carries the per-user onboarding flag (value depends on store).
    assert isinstance(body["onboarding_completed"], bool)


def test_auth_off_ignores_any_authorization_header():
    # With auth off the dependency short-circuits before ever looking at the
    # header, so even a junk bearer value still yields the default user.
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == DEFAULT_USER_ID


def test_auth_enabled_helper_reads_env_at_call_time(monkeypatch):
    assert auth_mod.auth_enabled() is False
    monkeypatch.setenv("AUTH_ENABLED", "true")
    assert auth_mod.auth_enabled() is True
    monkeypatch.setenv("AUTH_ENABLED", "0")
    assert auth_mod.auth_enabled() is False


# ─── auth ON ─────────────────────────────────────────────────────────────────


def test_auth_on_missing_token_401(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_auth_on_garbage_token_401(monkeypatch, use_fake_jwks):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


def test_auth_on_malformed_header_401(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    # Missing the "Bearer " scheme entirely.
    resp = client.get("/auth/me", headers={"Authorization": "abc123"})
    assert resp.status_code == 401


def test_auth_on_valid_token_returns_sub(monkeypatch, use_fake_jwks, rsa_keypair):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    token = _make_token(rsa_keypair, sub="user_xyz789")
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "user_xyz789"
    assert body["auth_enabled"] is True
    assert isinstance(body["onboarding_completed"], bool)


def test_auth_on_expired_token_401(monkeypatch, use_fake_jwks, rsa_keypair):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    token = _make_token(rsa_keypair, exp_delta=-60)  # expired a minute ago
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_auth_on_token_without_sub_401(monkeypatch, use_fake_jwks, rsa_keypair):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    token = _make_token(rsa_keypair, drop_sub=True)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_auth_on_wrong_signature_401(monkeypatch, use_fake_jwks):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    # Sign with a DIFFERENT key than the verifier's public key.
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _make_token(other_key)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_auth_on_issuer_enforced_when_configured(monkeypatch, use_fake_jwks, rsa_keypair):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.alpha-terminal.test")

    # Right issuer -> accepted.
    good = _make_token(rsa_keypair, iss="https://clerk.alpha-terminal.test")
    ok = client.get("/auth/me", headers={"Authorization": f"Bearer {good}"})
    assert ok.status_code == 200

    # Wrong issuer -> rejected.
    bad = _make_token(rsa_keypair, iss="https://clerk.evil.test")
    nope = client.get("/auth/me", headers={"Authorization": f"Bearer {bad}"})
    assert nope.status_code == 401


def test_auth_on_no_jwks_configured_is_500(monkeypatch, rsa_keypair):
    # Auth on, a syntactically-real token presented, but the server has no
    # CLERK_JWKS_URL / CLERK_ISSUER — a deploy misconfiguration, not a bad token.
    monkeypatch.setenv("AUTH_ENABLED", "1")
    token = _make_token(rsa_keypair)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 500


def test_auth_on_within_leeway_accepted(monkeypatch, use_fake_jwks, rsa_keypair):
    # Expired 10s ago but inside the 30s clock-skew leeway -> still accepted.
    monkeypatch.setenv("AUTH_ENABLED", "1")
    token = _make_token(rsa_keypair, exp_delta=-10)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# ─── real PyJWKClient path (kid matching + caching, no network) ───────────────


def test_real_jwks_client_resolves_kid(monkeypatch, rsa_keypair):
    """Drive the actual PyJWKClient: a token with a `kid` is verified against an
    in-memory JWKS document, exercising key lookup that the fake stub skips."""
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    kid = "test-kid-1"
    jwks = _build_jwks(rsa_keypair.public_key(), kid)
    # Patch only the network fetch, leaving kid-matching + decode real.
    monkeypatch.setattr(auth_mod.PyJWKClient, "fetch_data", lambda self: jwks)

    token = _make_token(rsa_keypair, sub="user_real", kid=kid)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "user_real"


def test_real_jwks_client_unknown_kid_401(monkeypatch, rsa_keypair):
    """A token whose `kid` isn't in the published JWKS (e.g. stale cache after a
    rotation, or a foreign Clerk instance) is rejected, not 500."""
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    jwks = _build_jwks(rsa_keypair.public_key(), "published-kid")
    monkeypatch.setattr(auth_mod.PyJWKClient, "fetch_data", lambda self: jwks)

    token = _make_token(rsa_keypair, kid="some-other-kid")
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_jwks_client_cached_per_url(monkeypatch):
    """_get_jwks_client memoizes one client per URL: same URL -> same instance,
    different URL -> a fresh one."""
    monkeypatch.setenv("CLERK_JWKS_URL", "https://a.test/.well-known/jwks.json")
    first = auth_mod._get_jwks_client()
    second = auth_mod._get_jwks_client()
    assert first is second

    monkeypatch.setenv("CLERK_JWKS_URL", "https://b.test/.well-known/jwks.json")
    third = auth_mod._get_jwks_client()
    assert third is not first


# ─── verify_clerk_token (direct unit-level contract) ─────────────────────────


def test_verify_clerk_token_returns_full_claims(monkeypatch, use_fake_jwks, rsa_keypair):
    token = _make_token(rsa_keypair, sub="user_direct")
    claims = auth_mod.verify_clerk_token(token)
    assert claims["sub"] == "user_direct"
    assert "exp" in claims


def test_verify_clerk_token_raises_auth_error_on_bad_token(monkeypatch, use_fake_jwks):
    with pytest.raises(auth_mod.ClerkAuthError):
        auth_mod.verify_clerk_token("not.a.jwt")


def test_verify_clerk_token_raises_config_error_without_jwks(monkeypatch, rsa_keypair):
    # No CLERK_JWKS_URL / CLERK_ISSUER configured -> server misconfig, not 401.
    token = _make_token(rsa_keypair)
    with pytest.raises(auth_mod.ClerkConfigError):
        auth_mod.verify_clerk_token(token)
