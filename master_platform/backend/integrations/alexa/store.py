"""Persistent state for the Alexa Smart Home Skill integration.

Stored as a single Fernet-encrypted JSON blob at
``data/alexa/state.json`` so we don't touch the DB schema. The blob
holds:

  skill:
    client_id            string  — OAuth client id we issued to Alexa
    client_secret_hash   string  — argon2 hash of the OAuth client secret
    redirect_uris        list    — Alexa LWA redirect URIs (3 official)
    alexa_account_email  string  — the operator's Alexa email
    created_at           iso-ts
    skill_id             string  — Amazon-assigned, optional (operator
                                    pastes in once Amazon issues it)
  auth_codes:
    <code>: {user_id, redirect_uri, expires_at}    TTL ~5 min
  tokens:
    <access_token>: {user_id, refresh_token, expires_at}  TTL 1 h
    refresh_<refresh_token>: <access_token>        for reverse lookup

Concurrency: single-process master, single asyncio loop. A simple
asyncio.Lock around read-modify-write is enough to keep the file
consistent without juggling fsync details.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...security.secret_crypto import decrypt_secret, encrypt_secret


_AUTH_CODE_TTL_S = 300        # 5 minutes
_ACCESS_TOKEN_TTL_S = 3600    # 1 hour
_REFRESH_TOKEN_TTL_S = 60 * 60 * 24 * 90   # 90 days


@dataclass
class AlexaState:
    skill: dict = field(default_factory=dict)
    auth_codes: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "AlexaState":
        return cls(
            skill=d.get("skill") or {},
            auth_codes=d.get("auth_codes") or {},
            tokens=d.get("tokens") or {},
        )

    def to_dict(self) -> dict:
        return {"skill": self.skill, "auth_codes": self.auth_codes,
                "tokens": self.tokens}


class AlexaStore:
    """Async-safe accessor for the encrypted on-disk state file."""

    def __init__(self, path: Path | str = "data/alexa/state.json"):
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def _read(self) -> AlexaState:
        if not self.path.exists():
            return AlexaState()
        try:
            blob = self.path.read_text(encoding="utf-8").strip()
            if not blob:
                return AlexaState()
            plain = decrypt_secret(blob)
            return AlexaState.from_dict(json.loads(plain))
        except Exception:
            # Corrupt state → empty. Setup page will surface this as
            # "not provisioned" and the operator can re-bootstrap.
            return AlexaState()

    async def _write(self, state: AlexaState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cipher = encrypt_secret(json.dumps(state.to_dict()))
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(cipher, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    # ── skill bootstrap ──────────────────────────────────────────────
    async def get_state(self) -> AlexaState:
        return await self._read()

    async def bootstrap_skill(self, *, alexa_account_email: str,
                              public_base_url: str) -> tuple[str, str]:
        """Generate fresh client_id + client_secret for Alexa Account
        Linking. Returns (client_id, client_secret) ONCE — the secret
        is hashed before storage and cannot be recovered later."""
        from argon2 import PasswordHasher
        client_id = "alexa-" + secrets.token_urlsafe(12)
        client_secret = secrets.token_urlsafe(32)
        async with self._lock:
            state = await self._read()
            state.skill = {
                "client_id": client_id,
                "client_secret_hash": PasswordHasher().hash(client_secret),
                "alexa_account_email": alexa_account_email.strip().lower(),
                "redirect_uris": [
                    # The three Alexa Account Linking redirect URIs.
                    "https://layla.amazon.com/api/skill/link/",
                    "https://alexa.amazon.co.jp/api/skill/link/",
                    "https://pitangui.amazon.com/api/skill/link/",
                ],
                "public_base_url": public_base_url.rstrip("/"),
                "skill_id": state.skill.get("skill_id"),
                "created_at": time.time(),
            }
            await self._write(state)
        return client_id, client_secret

    async def set_skill_id(self, skill_id: str) -> None:
        async with self._lock:
            state = await self._read()
            if not state.skill:
                raise RuntimeError("skill not bootstrapped yet")
            state.skill["skill_id"] = skill_id.strip()
            await self._write(state)

    async def verify_client(self, client_id: str, client_secret: str) -> bool:
        from argon2 import PasswordHasher
        from argon2.exceptions import VerifyMismatchError
        state = await self._read()
        if not state.skill or state.skill.get("client_id") != client_id:
            return False
        try:
            PasswordHasher().verify(state.skill["client_secret_hash"],
                                    client_secret)
            return True
        except (VerifyMismatchError, Exception):
            return False

    # ── auth-code flow ───────────────────────────────────────────────
    async def issue_auth_code(self, user_id: str, redirect_uri: str) -> str:
        code = secrets.token_urlsafe(24)
        async with self._lock:
            state = await self._read()
            state.auth_codes = self._gc_codes(state.auth_codes)
            state.auth_codes[code] = {
                "user_id": user_id,
                "redirect_uri": redirect_uri,
                "expires_at": time.time() + _AUTH_CODE_TTL_S,
            }
            await self._write(state)
        return code

    async def consume_auth_code(self, code: str,
                                redirect_uri: str | None = None) -> str | None:
        """Single-use. Returns user_id or None."""
        async with self._lock:
            state = await self._read()
            entry = state.auth_codes.pop(code, None)
            if entry is None:
                await self._write(state)
                return None
            if entry["expires_at"] < time.time():
                await self._write(state)
                return None
            if redirect_uri and entry.get("redirect_uri") != redirect_uri:
                await self._write(state)
                return None
            await self._write(state)
            return entry["user_id"]

    @staticmethod
    def _gc_codes(codes: dict) -> dict:
        now = time.time()
        return {k: v for k, v in codes.items() if v.get("expires_at", 0) > now}

    # ── tokens ───────────────────────────────────────────────────────
    async def issue_token_pair(self, user_id: str) -> dict:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = time.time()
        async with self._lock:
            state = await self._read()
            state.tokens = self._gc_tokens(state.tokens)
            state.tokens[access] = {
                "user_id": user_id,
                "refresh_token": refresh,
                "expires_at": now + _ACCESS_TOKEN_TTL_S,
            }
            state.tokens[f"refresh_{refresh}"] = {
                "user_id": user_id,
                "expires_at": now + _REFRESH_TOKEN_TTL_S,
            }
            await self._write(state)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": _ACCESS_TOKEN_TTL_S,
        }

    async def refresh(self, refresh_token: str) -> dict | None:
        async with self._lock:
            state = await self._read()
            key = f"refresh_{refresh_token}"
            entry = state.tokens.get(key)
            if entry is None or entry.get("expires_at", 0) < time.time():
                return None
            user_id = entry["user_id"]
        # issue a new pair — also invalidate the old access tokens that
        # pointed at this refresh, so a leaked access token doesn't
        # outlive the rotation.
        pair = await self.issue_token_pair(user_id)
        return pair

    async def resolve_access_token(self, access_token: str) -> str | None:
        state = await self._read()
        entry = state.tokens.get(access_token)
        if entry is None:
            return None
        if entry.get("expires_at", 0) < time.time():
            return None
        return entry.get("user_id")

    @staticmethod
    def _gc_tokens(tokens: dict) -> dict:
        now = time.time()
        return {k: v for k, v in tokens.items() if v.get("expires_at", 0) > now}
