"""OTA signing + manifest verification + version-gate tests."""
from __future__ import annotations

import json

import pytest


def test_signing_roundtrip(isolated_master):
    from backend.security import signing

    msg = b"firmware bundle manifest payload"
    sig = signing.sign_b64(msg)
    assert sig["alg"] == "ed25519"
    assert sig["kid"]
    assert signing.verify_b64(msg, sig["value"]) is True


def test_signing_rejects_tamper(isolated_master):
    from backend.security import signing

    sig = signing.sign_b64(b"original")
    assert signing.verify_b64(b"original", sig["value"]) is True
    assert signing.verify_b64(b"tampered", sig["value"]) is False


def test_pubkey_endpoint_returns_consistent_key_after_restart(isolated_master):
    """The signing seed lives at data/ota_signing.key; same dir = same key."""
    from backend.security import signing

    pub_a = signing.public_key_b64()

    # Force re-import of the module (simulates process restart).
    import importlib, sys
    sys.modules.pop("backend.security.signing", None)
    from backend.security import signing as signing_b
    pub_b = signing_b.public_key_b64()
    assert pub_a == pub_b


def test_ota_manifest_signature_verifies_via_pico_path(isolated_master):
    """Build a manifest with the Master's signing key, then verify it with
    the Pico's stand-alone Ed25519 verifier."""
    from backend.security import signing as ms
    from ed25519_verify import verify  # picked up via sys.path = level0-pico2w

    manifest = {
        "manifest_id": "bundle-X-1.0.0",
        "kind": "app_bundle",
        "issued_at": "2026-05-09T12:00:00Z",
        "issuer": "master_platform",
        "channel": "stable",
        "min_supported_base_firmware_version": "1.0.0",
        "supported_hardware_revisions": ["rev_a"],
        "target_app_bundle_version": "1.1.0",
        "target_config_schema_version": "1.0.0",
        "rollback_allowed": False,
        "drivers": [],
        "artifacts": [{"path": "dna.json", "size_bytes": 1, "sha256": "0" * 64, "compression": "none"}],
    }
    canon = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["signature"] = ms.sign_b64(canon)

    import base64
    pub = base64.b64decode(ms.public_key_b64())
    sig_bytes = base64.b64decode(manifest["signature"]["value"])
    assert verify(pub, canon, sig_bytes) is True

    # Modify after signing → must fail.
    manifest["target_app_bundle_version"] = "99.0.0"
    canon_tampered = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert verify(pub, canon_tampered, sig_bytes) is False


@pytest.mark.parametrize(
    "device_hw,supported,expected",
    [
        ("rev_a", ["rev_a"], True),
        ("rev_a", ["rev_b", "rev_c"], False),
        ("rev_z", ["rev_a"], False),
    ],
)
def test_pico_compat_hardware_gate(isolated_master, device_hw, supported, expected):
    from ota_client import check_compatibility

    class DNA:
        hardware_revision = device_hw
        base_firmware_version = "1.0.0"
        app_bundle_version = "1.0.0"

    manifest = {
        "supported_hardware_revisions": supported,
        "min_supported_base_firmware_version": "1.0.0",
        "target_app_bundle_version": "1.0.0",
        "rollback_allowed": False,
    }
    ok, reason = check_compatibility(manifest, DNA())
    assert ok is expected, reason


def test_pico_compat_base_too_low(isolated_master):
    from ota_client import check_compatibility

    class DNA:
        hardware_revision = "rev_a"
        base_firmware_version = "0.5.0"
        app_bundle_version = "1.0.0"

    manifest = {
        "supported_hardware_revisions": ["rev_a"],
        "min_supported_base_firmware_version": "1.0.0",
        "target_app_bundle_version": "1.0.0",
        "rollback_allowed": False,
    }
    ok, reason = check_compatibility(manifest, DNA())
    assert ok is False
    assert "base_firmware_version" in reason


def test_pico_downgrade_requires_rollback_allowed(isolated_master):
    from ota_client import check_compatibility

    class DNA:
        hardware_revision = "rev_a"
        base_firmware_version = "1.0.0"
        app_bundle_version = "2.0.0"

    manifest = {
        "supported_hardware_revisions": ["rev_a"],
        "min_supported_base_firmware_version": "1.0.0",
        "target_app_bundle_version": "1.5.0",
        "rollback_allowed": False,
    }
    ok, _ = check_compatibility(manifest, DNA())
    assert ok is False

    manifest["rollback_allowed"] = True
    ok, _ = check_compatibility(manifest, DNA())
    assert ok is True
