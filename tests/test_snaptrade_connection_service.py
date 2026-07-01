"""SnapTrade connection storage — identical behavior under file and DB backends,
with the secret encrypted at rest when a key is configured.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend.database.connection import Base
import app.backend.database.app_models  # noqa: F401  (register tables on Base)
from app.backend.services import _storage
from app.backend.services import snaptrade_connection_service as svc


@pytest.fixture()
def enc_key(monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    yield


@pytest.fixture()
def db_backend(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_storage, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setenv("STORAGE_BACKEND", "db")
    try:
        yield
    finally:
        engine.dispose()


@pytest.fixture()
def file_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(svc, "_STORE_PATH", tmp_path / "snaptrade_connections.json")
    yield


def _roundtrip() -> dict:
    out: dict = {}
    out["initial_status"] = svc.get_status()
    out["initial_creds"] = svc.get_credentials()
    saved = svc.save("stu-1", "secret-value")
    out["saved_connected"] = saved["connected"]
    out["saved_user"] = saved["snaptrade_user_id"]
    # metadata never leaks the secret
    status = svc.get_status()
    out["status_has_no_secret"] = "user_secret" not in status
    out["status_connected"] = status["connected"]
    out["creds"] = svc.get_credentials()
    # replace
    svc.save("stu-1", "rotated-secret")
    out["creds_after_rotate"] = svc.get_credentials()
    out["deleted"] = svc.delete()
    out["delete_again"] = svc.delete()
    out["final_status"] = svc.get_status()
    return out


def test_file_backend_roundtrip(file_backend, enc_key):
    result = _roundtrip()
    assert result["initial_status"] is None
    assert result["initial_creds"] is None
    assert result["saved_connected"] is True
    assert result["saved_user"] == "stu-1"
    assert result["status_has_no_secret"] is True
    assert result["creds"] == ("stu-1", "secret-value")
    assert result["creds_after_rotate"] == ("stu-1", "rotated-secret")
    assert result["deleted"] is True
    assert result["delete_again"] is False
    assert result["final_status"] is None


def test_db_backend_roundtrip(db_backend, enc_key):
    result = _roundtrip()
    assert result["initial_status"] is None
    assert result["creds"] == ("stu-1", "secret-value")
    assert result["creds_after_rotate"] == ("stu-1", "rotated-secret")
    assert result["deleted"] is True
    assert result["final_status"] is None


def test_file_backend_encrypts_secret_at_rest_when_key_set(file_backend, enc_key):
    import json

    svc.save("stu-1", "top-secret")
    raw = json.loads(svc._STORE_PATH.read_text())
    record = raw["default"]  # DEFAULT_USER_ID (auth off)
    assert record["encrypted"] is True
    assert "top-secret" not in record["user_secret"]  # ciphertext, not plaintext
    assert svc.get_credentials() == ("stu-1", "top-secret")


def test_file_backend_plaintext_fallback_without_key(file_backend, monkeypatch):
    import json

    monkeypatch.delenv("API_KEY_ENCRYPTION_KEY", raising=False)
    svc.save("stu-1", "top-secret")
    record = json.loads(svc._STORE_PATH.read_text())["default"]
    assert record["encrypted"] is False
    assert record["user_secret"] == "top-secret"
    assert svc.get_credentials() == ("stu-1", "top-secret")
