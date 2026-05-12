"""Edge-side WiFi + device scanning.

These run ON the edge runtime (a Raspberry Pi or equivalent that's
actually on the local network). The Master proxies scan requests
here from the device-registration wizard so operators see the
networks/devices the EDGE can see, not what the Master sees (which
is nothing — Master lives in a datacenter).

Two shapes:
  POST /api/v1/scan/wifi      → list of {ssid, signal, security}
  POST /api/v1/scan/devices   → list of {ip, mac, hostname, dna_hint}

Both gracefully degrade: if the host tools aren't available (no
WiFi adapter, no nmap installed), we return an empty list with a
'note' field explaining why so the UI can show that clearly.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from fastapi import APIRouter


router = APIRouter(prefix="/api/v1/scan", tags=["scan"])


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


def _scan_wifi_nmcli() -> list[dict[str, Any]] | None:
    if shutil.which("nmcli") is None:
        return None
    # Force a rescan so we don't get a stale cache.
    _run(["nmcli", "dev", "wifi", "rescan"], timeout=10)
    rc, out, _ = _run(
        ["nmcli", "-t", "-e", "no", "-f", "SSID,BSSID,SIGNAL,SECURITY,FREQ", "dev", "wifi"],
        timeout=15,
    )
    if rc != 0:
        return None
    seen: dict[str, dict[str, Any]] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        # nmcli -t separator is ':' but BSSIDs contain ':' too — split on unescaped colons.
        parts = re.split(r"(?<!\\):", line)
        # Unescape '\:' → ':' inside fields.
        parts = [p.replace("\\:", ":") for p in parts]
        if len(parts) < 5:
            continue
        ssid, bssid, sig, sec, freq = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not ssid:
            continue
        # Dedup by SSID keeping strongest signal.
        try:
            sig_i = int(sig)
        except ValueError:
            sig_i = 0
        existing = seen.get(ssid)
        if existing and existing["signal"] >= sig_i:
            continue
        seen[ssid] = {
            "ssid": ssid,
            "bssid": bssid,
            "signal": sig_i,
            "security": (sec or "open").lower(),
            "frequency_mhz": int(freq) if freq.isdigit() else None,
        }
    return sorted(seen.values(), key=lambda r: -r["signal"])


def _scan_wifi_iwlist() -> list[dict[str, Any]] | None:
    if shutil.which("iwlist") is None:
        return None
    # Best-effort iface guess. wlan0 covers Pi default; wlp* covers Debian.
    for iface in ("wlan0", "wlp2s0", "wlp3s0"):
        rc, out, _ = _run(["iwlist", iface, "scan"], timeout=20)
        if rc == 0:
            break
    else:
        return None
    networks: list[dict[str, Any]] = []
    cell: dict[str, Any] = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Cell "):
            if cell:
                networks.append(cell)
            cell = {"ssid": "", "signal": 0, "security": "open"}
        elif line.startswith("ESSID:"):
            cell["ssid"] = line.split(":", 1)[1].strip().strip('"')
        elif "Signal level=" in line:
            m = re.search(r"Signal level=(-?\d+)", line)
            if m:
                cell["signal"] = int(m.group(1))
        elif "Encryption key:on" in line:
            cell.setdefault("security", "wpa2")
    if cell:
        networks.append(cell)
    return [n for n in networks if n.get("ssid")]


@router.post("/wifi")
async def scan_wifi() -> dict[str, Any]:
    """Scan visible WiFi networks. Tries nmcli first, falls back to iwlist."""
    results = _scan_wifi_nmcli()
    note = None
    if results is None:
        results = _scan_wifi_iwlist()
    if results is None:
        results = []
        note = ("Neither 'nmcli' nor 'iwlist' is installed on this edge, "
                "or the WiFi interface couldn't be queried. Install "
                "network-manager (for nmcli) or wireless-tools (for iwlist).")
    return {"networks": results, "count": len(results), "note": note}


@router.post("/devices")
async def scan_devices() -> dict[str, Any]:
    """Scan the local subnet for reachable hosts.

    Uses nmap if available (cheap ARP ping); falls back to 'ip neigh' which
    only sees hosts the edge has recently talked to. Returns a list of
    {ip, mac, hostname} candidates the operator can then claim as Neuron
    devices.
    """
    candidates: list[dict[str, Any]] = []
    note = None
    if shutil.which("nmap") is not None:
        # ARP-only ping sweep on the edge's primary subnet.
        rc, out, err = _run(["nmap", "-sn", "-PR", "-T4", "-oG", "-", "192.168.0.0/16"], timeout=25)
        if rc == 0:
            for line in out.splitlines():
                if not line.startswith("Host: "):
                    continue
                m = re.match(r"Host:\s+(\S+)\s+\(([^)]*)\)\s+Status:\s+Up", line)
                if m:
                    candidates.append({"ip": m.group(1), "hostname": m.group(2) or None})
        else:
            note = f"nmap returned non-zero: {err.strip()[:200]}"
    if not candidates and shutil.which("ip") is not None:
        rc, out, _ = _run(["ip", "neigh"], timeout=5)
        if rc == 0:
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1] == "dev":
                    candidates.append({"ip": parts[0], "mac": parts[4], "hostname": None})
    if not candidates and note is None:
        note = "No scan tools available (install nmap for a proper sweep)."
    return {"candidates": candidates, "count": len(candidates), "note": note}
