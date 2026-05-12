"""Symmetric encryption helpers for at-rest secrets (WiFi passwords).

Reuses the deployment's session secret as input to derive a Fernet key
so we don't have a second key-management surface. Plaintext only ever
appears in memory at firmware-build time and in the admin UI when a
user is intentionally editing a row — never persisted in cleartext.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


_SESSION_SECRET_PATH = Path("data/.session_secret")
_fernet_singleton: Fernet | None = None


def _derive_fernet_key(seed: str) -> bytes:
    """Fernet wants a 32-byte url-safe base64 key. SHA-256 of the seed
    gives us a deterministic 32-byte value we can encode."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    global _fernet_singleton
    if _fernet_singleton is not None:
        return _fernet_singleton
    if _SESSION_SECRET_PATH.exists():
        seed = _SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()
    else:
        seed = "neuron-default-do-not-use-in-prod"
    _fernet_singleton = Fernet(_derive_fernet_key(seed))
    return _fernet_singleton


def encrypt_secret(plaintext: str | None) -> str | None:
    """Encrypt a string for storage in the DB. None passes through."""
    if plaintext is None or plaintext == "":
        return None
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str | None) -> str | None:
    """Decrypt a value previously written by ``encrypt_secret``.
    Returns None on InvalidToken (e.g. key rotation without rewrap)."""
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None
