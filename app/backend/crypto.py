"""Symmetric encryption for secrets at rest (Phase 3 — BYOK API keys).

User-supplied provider API keys (DeepSeek, Massive, Finnhub) are stored in the
``api_keys`` table. In a multi-tenant deployment those must never sit in the
database as plaintext, so the repository encrypts each key with Fernet
(AES-128-CBC + HMAC authentication) before writing and decrypts on read.

The Fernet key comes from the ``API_KEY_ENCRYPTION_KEY`` environment variable, a
urlsafe-base64-encoded 32-byte key (generate one with
``Fernet.generate_key().decode()`` — see README/DEPLOY). It is read **at call
time** so a deploy supplies it via the environment and tests can set a throwaway
key per-case; it is never logged.

Rotation is supported: ``API_KEY_ENCRYPTION_KEY`` may hold several keys separated
by commas. The **first** is used to encrypt; **all** are tried on decrypt
(:class:`cryptography.fernet.MultiFernet`), so you can add a new key at the front,
re-encrypt lazily, and retire the old one later.

Errors are explicit (no silent swallow, per repo convention):

- :class:`EncryptionNotConfigured` — no key set when one is needed (a server
  misconfiguration; the route maps it to 500).
- :class:`DecryptionError` — a stored value can't be decrypted with any current
  key (corruption, or the key was rotated out).
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

__all__ = [
    "encrypt",
    "decrypt",
    "encryption_configured",
    "EncryptionNotConfigured",
    "DecryptionError",
]


class EncryptionNotConfigured(Exception):
    """``API_KEY_ENCRYPTION_KEY`` is unset/blank but encryption was requested."""


class DecryptionError(Exception):
    """A stored ciphertext could not be decrypted with any configured key."""


def _load_keys() -> list[str]:
    raw = os.environ.get("API_KEY_ENCRYPTION_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def encryption_configured() -> bool:
    """True when at least one Fernet key is configured."""
    return bool(_load_keys())


def _multifernet() -> MultiFernet:
    keys = _load_keys()
    if not keys:
        raise EncryptionNotConfigured(
            "API_KEY_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and set it in the environment before storing user API keys."
        )
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except (ValueError, TypeError) as exc:
        # A malformed key is a deploy mistake, not a runtime data problem.
        raise EncryptionNotConfigured(f"API_KEY_ENCRYPTION_KEY is malformed: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a secret, returning a urlsafe-base64 token (str) to store."""
    return _multifernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt`. Raises :class:`DecryptionError`
    if no configured key can authenticate it."""
    try:
        return _multifernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError(
            "Stored API key could not be decrypted (corrupted, or the encryption "
            "key was rotated out)."
        ) from exc
