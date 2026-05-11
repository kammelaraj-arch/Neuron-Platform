"""
Firmware bundle builder.

Produces a deterministic .zip bundle for a device that contains:
  - dna.json, brain.json, pinmap.json
  - manifest.json (bundle-level)
  - drivers manifest (modules required by the firmware to load)
  - a firmware bootstrap stub for MicroPython (config-driven)

The bundle is sized small enough for OTA delivery.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .library_loader import LibraryCatalog
from .security import signing


_STUB_MAIN_PY = '''"""
Auto-generated firmware bootstrap for Neuron Platform.
Loads dna.json, brain.json and pinmap.json, then enters main.py from /level0-pico2w.
"""
import json, sys

with open("dna.json") as fh:
    DNA = json.load(fh)
with open("brain.json") as fh:
    BRAIN = json.load(fh)
with open("pinmap.json") as fh:
    PINMAP = json.load(fh)

print("[neuron] booted", DNA["device_dna"], "fw", DNA["base_firmware_version"])
'''


def _deterministic_zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def build_bundle(
    *,
    device_dna: str,
    dna: dict[str, Any],
    brain: dict[str, Any],
    pinmap: dict[str, Any],
    catalog: LibraryCatalog,
) -> tuple[Path, dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for comp in dna.get("components", []):
        item = catalog.get(comp["stable_id"])
        if item is None:
            continue
        for drv in item.manifest.get("drivers", []) or []:
            key = (drv["runtime"], drv["module"])
            if key in seen:
                continue
            seen.add(key)
            drivers.append(drv)

    bundle_manifest = {
        "manifest_id": f"bundle-{device_dna}-{dna['app_bundle_version']}",
        "kind": "app_bundle",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "issuer": "master_platform",
        "channel": "stable",
        "min_supported_base_firmware_version": dna["base_firmware_version"],
        "supported_hardware_revisions": [dna["hardware_revision"]],
        "target_app_bundle_version": dna["app_bundle_version"],
        "target_config_schema_version": dna["config_schema_version"],
        "rollback_allowed": False,
        "drivers": drivers,
    }

    artifacts_dir = settings.build_artifacts_dir
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # Versioned per app_bundle_version so older releases stay around for
    # rollback. The plain `<DNA>.zip` name remains as the channel-active
    # symlink-equivalent (rewritten on every successful promote).
    versioned_path = artifacts_dir / f"{device_dna}-{dna['app_bundle_version']}.zip"
    bundle_path = versioned_path

    files: dict[str, bytes] = {
        "dna.json": json.dumps(dna, sort_keys=True, indent=2).encode("utf-8"),
        "brain.json": json.dumps(brain, sort_keys=True, indent=2).encode("utf-8"),
        "pinmap.json": json.dumps(pinmap, sort_keys=True, indent=2).encode("utf-8"),
        "manifest.json": json.dumps(bundle_manifest, sort_keys=True, indent=2).encode("utf-8"),
        "main_stub.py": _STUB_MAIN_PY.encode("utf-8"),
    }

    # Compute artifact hashes prior to writing into the zip.
    artifacts_meta = []
    for name, data in files.items():
        sha = hashlib.sha256(data).hexdigest()
        artifacts_meta.append(
            {"path": name, "size_bytes": len(data), "sha256": sha, "compression": "none"}
        )
    bundle_manifest["artifacts"] = artifacts_meta

    # Sign the manifest. We sign the canonical form (sorted keys, no spaces)
    # before adding the signature so verification is straightforward: drop
    # the .signature field and re-canonicalise.
    canon = json.dumps(bundle_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    bundle_manifest["signature"] = signing.sign_b64(canon)

    # Re-serialise manifest.json now that artifacts + signature are final.
    manifest_bytes = json.dumps(bundle_manifest, sort_keys=True, indent=2).encode("utf-8")
    files["manifest.json"] = manifest_bytes

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            _deterministic_zip_write(zf, name, files[name])

    bundle_bytes = buf.getvalue()
    bundle_path.write_bytes(bundle_bytes)
    bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()

    return bundle_path, {
        "manifest": bundle_manifest,
        "size_bytes": len(bundle_bytes),
        "sha256": bundle_sha,
    }
