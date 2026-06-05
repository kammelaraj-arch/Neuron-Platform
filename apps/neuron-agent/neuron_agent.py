#!/usr/bin/env python3
"""Neuron pull-agent — runs on a remote device (Pi 4 / Pi 5).

Polls the Neuron Master for queued commands, executes them locally,
reports the result back. The device opens ZERO inbound ports — all
traffic is outbound HTTPS, which works through any home / factory NAT.

Configured by three environment variables (set them in the systemd
unit or /etc/default/neuron-agent):

    NEURON_MASTER_URL    = https://neuron.shital.org.uk
    NEURON_DEVICE_ID     = <uuid printed at /ui/plotter>
    NEURON_AGENT_TOKEN   = <bearer token shown when device was added>

Optional:
    NEURON_LOCAL_BASE    = http://127.0.0.1:5001         (SmartPlotter base)
    NEURON_LOCAL_API_KEY = <bearer the SmartPlotter expects>

Command kinds this agent knows (extend as needed):
    plotter.run    → POST {local}/api/profile/{profile_id}/run
    plotter.abort  → POST {local}/api/profile/{profile_id}/abort
    plotter.home   → POST {local}/api/home

Robustness:
    - Backoff on Master-side errors (1s → 30s cap)
    - Catches every per-command exception so one bad command doesn't
      stop the loop. Errors are reported back to Master.
    - Honours the poll_interval_s the Master returns each poll so the
      operator can tune snappiness vs. bandwidth per device without
      editing this file.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request


_log = logging.getLogger("neuron-agent")


# ── config ──────────────────────────────────────────────────────────
MASTER_URL = (os.environ.get("NEURON_MASTER_URL") or "").rstrip("/")
DEVICE_ID = os.environ.get("NEURON_DEVICE_ID") or ""
AGENT_TOKEN = os.environ.get("NEURON_AGENT_TOKEN") or ""
LOCAL_BASE = (os.environ.get("NEURON_LOCAL_BASE") or "http://127.0.0.1:5001").rstrip("/")
LOCAL_API_KEY = os.environ.get("NEURON_LOCAL_API_KEY") or ""

DEFAULT_POLL_S = 3.0
MAX_BACKOFF_S = 30.0
HTTP_TIMEOUT_S = 8.0


def _check_config() -> None:
    missing = [k for k, v in (
        ("NEURON_MASTER_URL", MASTER_URL),
        ("NEURON_DEVICE_ID", DEVICE_ID),
        ("NEURON_AGENT_TOKEN", AGENT_TOKEN),
    ) if not v]
    if missing:
        sys.stderr.write(
            "ERROR: missing env vars: " + ", ".join(missing) + "\n"
            "Set them in /etc/default/neuron-agent (one KEY=value per line)\n"
        )
        sys.exit(2)


# ── HTTP helpers (stdlib only — keeps the agent dep-free) ───────────
def _request(method: str, url: str, *,
             headers: dict | None = None,
             body: dict | None = None,
             timeout: float = HTTP_TIMEOUT_S) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""
    except (urllib.error.URLError, socket.timeout) as e:
        raise ConnectionError(f"{method} {url} → {e}") from e


def _master_headers() -> dict:
    return {"Authorization": f"Bearer {AGENT_TOKEN}"}


def _local_headers() -> dict:
    return {"Authorization": f"Bearer {LOCAL_API_KEY}"} if LOCAL_API_KEY else {}


# ── command handlers ───────────────────────────────────────────────
def _handle_plotter_run(payload: dict) -> dict:
    pid = int(payload.get("profile_id"))
    code, body = _request("POST",
                          f"{LOCAL_BASE}/api/profile/{pid}/run",
                          headers=_local_headers())
    if code >= 400:
        raise RuntimeError(f"plotter run HTTP {code}: {body[:120].decode('utf-8','replace')}")
    return {"http_status": code, "body": body[:200].decode("utf-8", "replace")}


def _handle_plotter_abort(payload: dict) -> dict:
    pid = int(payload.get("profile_id"))
    code, body = _request("POST",
                          f"{LOCAL_BASE}/api/profile/{pid}/abort",
                          headers=_local_headers())
    if code >= 400:
        raise RuntimeError(f"plotter abort HTTP {code}: {body[:120].decode('utf-8','replace')}")
    return {"http_status": code}


def _handle_plotter_home(_payload: dict) -> dict:
    code, body = _request("POST",
                          f"{LOCAL_BASE}/api/home",
                          headers=_local_headers())
    if code >= 400:
        raise RuntimeError(f"plotter home HTTP {code}: {body[:120].decode('utf-8','replace')}")
    return {"http_status": code}


HANDLERS = {
    "plotter.run":   _handle_plotter_run,
    "plotter.abort": _handle_plotter_abort,
    "plotter.home":  _handle_plotter_home,
}


# ── main loop ──────────────────────────────────────────────────────
def _execute_one(cmd: dict) -> tuple[str, dict | None, str | None]:
    """Returns (status, result, error)."""
    kind = cmd.get("kind", "")
    handler = HANDLERS.get(kind)
    if handler is None:
        return "failed", None, f"unknown command kind {kind!r}"
    try:
        result = handler(cmd.get("payload") or {})
        return "done", result, None
    except Exception as e:
        return "failed", None, f"{type(e).__name__}: {e}"


def _report(cmd_id: str, status: str, result: dict | None, error: str | None) -> None:
    body = {"command_id": cmd_id, "status": status}
    if result is not None:
        body["result"] = result
    if error:
        body["error"] = error
    try:
        _request("POST",
                 f"{MASTER_URL}/api/agent/{DEVICE_ID}/report",
                 headers=_master_headers(), body=body)
    except Exception:
        _log.exception("failed to report command %s", cmd_id)


def run_forever() -> None:
    _check_config()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _log.info("neuron-agent online · master=%s device=%s", MASTER_URL, DEVICE_ID)

    poll_s = DEFAULT_POLL_S
    backoff = 1.0
    while True:
        try:
            code, body = _request(
                "POST",
                f"{MASTER_URL}/api/agent/{DEVICE_ID}/poll",
                headers=_master_headers(),
            )
            if code != 200:
                raise ConnectionError(f"poll HTTP {code}: {body[:120].decode('utf-8','replace')}")
            data = json.loads(body or b"{}")
            poll_s = float(data.get("poll_interval_s") or DEFAULT_POLL_S)
            backoff = 1.0
            for cmd in data.get("commands", []) or []:
                status, result, error = _execute_one(cmd)
                _report(cmd["id"], status, result, error)
                _log.info("cmd %s/%s → %s", cmd.get("kind"), cmd.get("id"), status)
        except Exception as e:
            _log.warning("poll cycle failed: %s (backoff %.1fs)", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2.0, MAX_BACKOFF_S)
            continue
        time.sleep(poll_s)


if __name__ == "__main__":
    run_forever()
