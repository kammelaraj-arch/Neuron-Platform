"""API key tier-derived scopes + audit-event recording."""
from __future__ import annotations

import asyncio

import pytest


def _scope_set(api_key):
    from backend.security.auth import SCOPE_HIERARCHY
    base = set(api_key.scopes_json or [])
    base.update(SCOPE_HIERARCHY.get(api_key.tier, set()))
    return base


@pytest.mark.parametrize(
    "tier,must_include,must_not_include",
    [
        ("admin",       {"admin", "apikeys:write", "audit:read"}, set()),
        ("integration", {"systems:write", "devices:write"},        {"apikeys:write", "audit:read", "admin"}),
        ("device",      {"devices:write", "library:read"},         {"apikeys:write", "systems:write"}),
        ("readonly",    {"library:read", "devices:read"},           {"devices:write", "systems:write"}),
    ],
)
def test_tier_scope_inheritance(isolated_master, tier, must_include, must_not_include):
    from backend.models import APIKey
    row = APIKey(
        id="t1", owner="t", tier=tier, scopes_json=[], rate_per_minute=60,
        rate_burst=5, status="active", hash_alg="argon2id", key_hash="x",
    )
    granted = _scope_set(row)
    assert must_include.issubset(granted), f"missing: {must_include - granted}"
    if must_not_include:
        assert not (granted & must_not_include), f"unexpectedly granted: {granted & must_not_include}"


def test_argon2_hash_verify_roundtrip(isolated_master):
    from backend.security.keys import generate_secret, hash_secret, verify_secret

    secret = generate_secret()
    assert secret.startswith("neu_") and len(secret) > 30
    h = hash_secret(secret)
    assert verify_secret(secret, h) is True
    assert verify_secret("neu_other-secret", h) is False
