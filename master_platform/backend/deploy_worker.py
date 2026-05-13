"""SSH-based device-deploy worker.

Takes a built firmware bundle (.zip from group_bundle.build_group_bundle)
and pushes it onto the target device using the SSH credentials captured
on the wizard's Review page.

Flow:
    1. Decrypt ssh_password / ssh_private_key / sudo_password from the
       Fernet-encrypted columns on EdgeGroup. Plaintext only exists in
       memory for the duration of this function.
    2. Open an asyncssh connection to (ssh_host or hostname, ssh_port).
    3. scp the bundle.zip to /tmp/neuron-<dna>.zip on the device.
    4. Run install.sh (shipped separately at /opt/neuron-agent/install.sh
       or fetched from this repo) as root via sudo.
    5. Tail the install log (last 30 lines) and verify
       neuron-agent.service is active.
    6. Return a DeployResult so the caller can audit + display per-step
       pass/fail.

Designed to NOT need any agent or pre-installed Neuron code on the
device — install.sh is self-bootstrapping. The only requirement is that
the device runs Debian-like Linux with Python 3.11+, nftables, and
systemd (i.e. stock Raspberry Pi OS).
"""
from __future__ import annotations

import dataclasses
import logging
import os
from pathlib import Path

from .models import EdgeGroup
from .security.secret_crypto import decrypt_secret


log = logging.getLogger("neuron.deploy_worker")


# Local path to the canonical install.sh that gets copied to the device
# alongside the bundle. Resolved at import time relative to this file
# so the worker doesn't depend on cwd.
_INSTALL_SH = (
    Path(__file__).resolve().parent.parent.parent
    / "device_runtime" / "install.sh"
)


@dataclasses.dataclass
class DeployStep:
    name: str
    ok: bool
    detail: str = ""
    ms: int | None = None


@dataclasses.dataclass
class DeployResult:
    ok: bool
    steps: list[DeployStep]
    device_dna: str
    host: str
    summary: str = ""


async def deploy_bundle_via_ssh(
    group: EdgeGroup,
    bundle_path: Path,
    timeout: int = 300,
) -> DeployResult:
    """Push `bundle_path` to the device described by `group` over SSH.

    Returns a DeployResult with one DeployStep per phase so the wizard
    can render a per-step ✓/✗ summary. Does NOT raise on failure —
    the caller decides what to do based on result.ok.
    """
    host = (group.ssh_host or group.hostname or group.local_ip or "").strip()
    if not host:
        return DeployResult(
            ok=False, device_dna=group.device_dna or "?", host="",
            steps=[DeployStep("validate_creds", False,
                              "no ssh_host / hostname / local_ip on group")],
            summary="SSH host not configured",
        )
    if not group.ssh_username:
        return DeployResult(
            ok=False, device_dna=group.device_dna or "?", host=host,
            steps=[DeployStep("validate_creds", False, "no ssh_username on group")],
            summary="SSH username not configured",
        )
    if not (group.ssh_password_encrypted or group.ssh_private_key_encrypted):
        return DeployResult(
            ok=False, device_dna=group.device_dna or "?", host=host,
            steps=[DeployStep("validate_creds", False,
                              "neither ssh_password nor ssh_private_key on group")],
            summary="SSH credentials not configured",
        )
    if not bundle_path.is_file():
        return DeployResult(
            ok=False, device_dna=group.device_dna or "?", host=host,
            steps=[DeployStep("validate_creds", False,
                              f"bundle not found at {bundle_path}")],
            summary="bundle .zip missing on master",
        )
    if not _INSTALL_SH.is_file():
        return DeployResult(
            ok=False, device_dna=group.device_dna or "?", host=host,
            steps=[DeployStep("validate_creds", False,
                              f"install.sh missing at {_INSTALL_SH}")],
            summary="install.sh missing on master",
        )

    try:
        import asyncssh
    except ImportError:
        return DeployResult(
            ok=False, device_dna=group.device_dna or "?", host=host,
            steps=[DeployStep("import_asyncssh", False,
                              "asyncssh not installed — pip install asyncssh")],
            summary="deploy worker dependency missing",
        )

    steps: list[DeployStep] = []
    port = int(group.ssh_port or 22)
    user = group.ssh_username

    # Decrypt creds only inside this function. Re-bind to plaintext
    # locals to keep their lifetime as short as possible.
    pwd = decrypt_secret(group.ssh_password_encrypted) if group.ssh_password_encrypted else None
    key_pem = decrypt_secret(group.ssh_private_key_encrypted) if group.ssh_private_key_encrypted else None
    sudo_pwd = decrypt_secret(group.sudo_password_encrypted) if group.sudo_password_encrypted else None

    connect_kwargs: dict = {
        "host": host, "port": port, "username": user,
        "known_hosts": None,                # accept on first connect
        "connect_timeout": 15,
    }
    if key_pem:
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(key_pem)]
    if pwd:
        connect_kwargs["password"] = pwd

    remote_zip = f"/tmp/neuron-{group.device_dna or group.id}.zip"
    remote_install = "/tmp/neuron-install.sh"

    async def _run(conn, cmd: str, become_root: bool = False,
                   timeout: int = 60) -> tuple[int, str, str]:
        if become_root and user != "root":
            # `sudo -S` reads the password from stdin.
            cmd = f"sudo -S -p '' bash -lc {_q(cmd)}"
            res = await conn.run(cmd, input=(sudo_pwd or pwd or "") + "\n",
                                 check=False, timeout=timeout)
        else:
            res = await conn.run(cmd, check=False, timeout=timeout)
        return res.exit_status or 0, (res.stdout or "").strip(), (res.stderr or "").strip()

    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            steps.append(DeployStep("ssh_connect", True,
                                    f"{user}@{host}:{port}"))

            # 1. Probe the device — is python3 present, is unzip present?
            rc, out, err = await _run(conn, "python3 --version && which unzip || true")
            steps.append(DeployStep("device_prereqs", rc == 0,
                                    out + (f" / {err}" if err else "")))
            if rc != 0:
                return DeployResult(ok=False, steps=steps,
                                    device_dna=group.device_dna or "?",
                                    host=host, summary="python3 missing on device")

            # 2. SCP the bundle.
            try:
                await asyncssh.scp(str(bundle_path), (conn, remote_zip),
                                   preserve=True, recurse=False)
                steps.append(DeployStep("scp_bundle", True, remote_zip))
            except Exception as e:
                steps.append(DeployStep("scp_bundle", False, str(e)[:200]))
                return DeployResult(ok=False, steps=steps,
                                    device_dna=group.device_dna or "?",
                                    host=host, summary="bundle scp failed")

            # 3. SCP install.sh.
            try:
                await asyncssh.scp(str(_INSTALL_SH), (conn, remote_install),
                                   preserve=True, recurse=False)
                steps.append(DeployStep("scp_install_sh", True, remote_install))
            except Exception as e:
                steps.append(DeployStep("scp_install_sh", False, str(e)[:200]))
                return DeployResult(ok=False, steps=steps,
                                    device_dna=group.device_dna or "?",
                                    host=host, summary="install.sh scp failed")

            # 4. Run install.sh as root.
            cmd = f"chmod +x {remote_install} && {remote_install} {remote_zip}"
            rc, out, err = await _run(conn, cmd, become_root=True, timeout=timeout)
            tail = (out + "\n" + err)[-1000:]
            steps.append(DeployStep("install_sh", rc == 0,
                                    tail.replace("\r", "")))
            if rc != 0:
                return DeployResult(ok=False, steps=steps,
                                    device_dna=group.device_dna or "?",
                                    host=host, summary=f"install.sh rc={rc}")

            # 5. Verify systemd unit is active.
            rc, out, err = await _run(conn,
                                      "systemctl is-active neuron-agent.service",
                                      become_root=False)
            steps.append(DeployStep("agent_active", out.strip() == "active", out))
            if out.strip() != "active":
                return DeployResult(ok=False, steps=steps,
                                    device_dna=group.device_dna or "?",
                                    host=host, summary="neuron-agent did not become active")

    except asyncssh.PermissionDenied as e:
        steps.append(DeployStep("ssh_connect", False, f"auth failed: {e}"))
        return DeployResult(ok=False, steps=steps,
                            device_dna=group.device_dna or "?",
                            host=host, summary="SSH authentication failed")
    except OSError as e:
        steps.append(DeployStep("ssh_connect", False, str(e)[:200]))
        return DeployResult(ok=False, steps=steps,
                            device_dna=group.device_dna or "?",
                            host=host, summary="SSH connect failed")

    return DeployResult(
        ok=True, steps=steps,
        device_dna=group.device_dna or "?", host=host,
        summary="bundle deployed + agent active",
    )


def _q(s: str) -> str:
    """Single-quote a string for the remote shell, escaping inner quotes."""
    return "'" + s.replace("'", "'\\''") + "'"
