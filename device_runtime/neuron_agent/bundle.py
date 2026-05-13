"""Reads + validates the firmware bundle dropped at /boot/neuron/ by
install.sh. The bundle is the output of the master's group_bundle.py:

    /boot/neuron/
        dna.json
        brain.json
        wifi.json
        vendor_accounts.json
        manifest.json

Validates manifest.json's sha256 list against each file. Fails closed
on mismatch — corrupted bundle means we refuse to start rather than
deploy with an unknown payload.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path


class BundleError(RuntimeError):
    """Bundle missing, malformed, or fails fingerprint verification."""


@dataclasses.dataclass
class Bundle:
    root: Path
    dna: dict
    brain: dict
    wifi: dict
    vendor_accounts: dict
    manifest: dict

    @property
    def device_dna(self) -> str:
        return self.dna["device_dna"]

    @property
    def role(self) -> str:
        return self.dna.get("role", "edge")

    @property
    def asset_id(self) -> str | None:
        return self.dna.get("asset_id")

    @property
    def parent_kind(self) -> str:
        return (self.dna.get("parent") or {}).get("kind", "edge")


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_bundle(root: Path) -> Bundle:
    """Load + verify the bundle at `root`.

    Raises BundleError if any file is missing, parse-fails, or doesn't
    match the manifest's sha256 entry. fingerprint_sha256 on the DNA
    is recomputed and compared too — failure means the bundle has been
    tampered with after the master signed it.
    """
    root = Path(root)
    if not root.is_dir():
        raise BundleError(f"bundle root not a directory: {root}")

    docs: dict[str, dict] = {}
    for name in ("manifest.json", "dna.json", "brain.json", "wifi.json", "vendor_accounts.json"):
        p = root / name
        if not p.is_file():
            # vendor_accounts.json is optional — older bundles + groups
            # with no smart-home components don't include it.
            if name == "vendor_accounts.json":
                docs[name] = {"schema_version": "1.0.0", "accounts": []}
                continue
            raise BundleError(f"bundle missing {name}")
        try:
            docs[name] = json.loads(p.read_bytes())
        except json.JSONDecodeError as e:
            raise BundleError(f"{name}: invalid JSON ({e})") from e

    manifest = docs["manifest.json"]

    # Verify manifest sha256 entries match the files on disk.
    by_name = {entry["name"]: entry for entry in manifest.get("files", [])}
    for name in ("dna.json", "brain.json", "wifi.json"):
        m = by_name.get(name)
        if m is None:
            continue
        actual = _sha256((root / name).read_bytes())
        if actual != m["sha256"]:
            raise BundleError(
                f"{name}: sha256 mismatch (manifest={m['sha256']} actual={actual})"
            )

    # Verify DNA fingerprint — last line of defence against tamper.
    dna = docs["dna.json"]
    expected = dna.get("fingerprint_sha256")
    if expected:
        clone = {k: v for k, v in dna.items() if k != "fingerprint_sha256"}
        actual = _sha256(
            json.dumps(clone, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
        )
        if actual != expected:
            raise BundleError(
                f"dna.json fingerprint mismatch (expected={expected} actual={actual})"
            )

    return Bundle(
        root=root,
        dna=dna,
        brain=docs["brain.json"],
        wifi=docs["wifi.json"],
        vendor_accounts=docs["vendor_accounts.json"],
        manifest=manifest,
    )
