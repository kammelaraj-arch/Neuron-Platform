"""Hardware-derived device identity. The wizard leaves dna.json's
device_dna blank when the operator doesn't pre-assign one; on first
boot the agent computes a deterministic ID from the compute module's
hardware fingerprint (Pi CPU serial + first MAC) and persists it
back."""
from __future__ import annotations

import hashlib
import json
import re
import socket
from pathlib import Path


_CPUINFO = Path("/proc/cpuinfo")


def cpu_serial() -> str | None:
    """Pi CPU serial from /proc/cpuinfo. None on non-Pi hosts."""
    if not _CPUINFO.is_file():
        return None
    try:
        for line in _CPUINFO.read_text().splitlines():
            m = re.match(r"^Serial\s*:\s*([0-9a-fA-F]+)\s*$", line)
            if m:
                return m.group(1).lower()
    except OSError:
        return None
    return None


def primary_mac() -> str | None:
    """MAC of the first non-loopback interface. Used as a fallback
    when /proc/cpuinfo doesn't expose a serial (e.g. on a generic Linux
    host running the dev agent)."""
    p = Path("/sys/class/net")
    if not p.is_dir():
        return None
    for iface in sorted(p.iterdir()):
        if iface.name == "lo":
            continue
        addr = iface / "address"
        if addr.is_file():
            try:
                return addr.read_text().strip().lower()
            except OSError:
                continue
    return None


def compute_uuid(prefix: str = "DNA") -> str:
    """Deterministic UUID for this compute. Hash of cpu_serial + mac so
    re-running on the same hardware yields the same DNA — important
    because the parent has registered this ID."""
    parts: list[str] = []
    cs = cpu_serial()
    if cs:
        parts.append(f"cpu:{cs}")
    mac = primary_mac()
    if mac:
        parts.append(f"mac:{mac}")
    if not parts:
        # Last-resort dev fallback — hostname only. Will differ across
        # hosts but at least reproducible per host.
        parts.append(f"host:{socket.gethostname()}")
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest().upper()
    return f"{prefix}-{h[0:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"


def populate_dna_if_blank(dna_path: Path) -> tuple[str, bool]:
    """If dna.json's device_dna is the wizard placeholder (None / empty
    / starts with 'DNA-' but says 'PENDING'), compute the hardware UUID
    and rewrite the file in place. Returns (final_dna, was_populated).
    """
    doc = json.loads(dna_path.read_text())
    current = (doc.get("device_dna") or "").strip()
    if current and not current.upper().endswith("PENDING"):
        return current, False
    new_id = compute_uuid()
    doc["device_dna"] = new_id
    dna_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return new_id, True
