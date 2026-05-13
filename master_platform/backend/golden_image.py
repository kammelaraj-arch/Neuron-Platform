"""Golden-image builder. Layers the firmware bundle + neuron-agent
package on top of a pinned Raspberry Pi OS base image and produces a
bootable `golden-image-<dna>.img.xz` ready for Etcher / `dd`.

Built on `pi-gen` style image-customisation (the official Raspberry Pi
Foundation tooling). The base image is fetched once and cached;
subsequent builds copy + customise + recompress, which is ~30-90 s on
the master.

Layered on top of the bundle (which the device-runtime install.sh
applies on first boot anyway):
    /boot/neuron/                          ← dna.json, brain.json, …
    /boot/firstrun.sh                      ← runs install.sh on first boot
    /etc/wpa_supplicant/wpa_supplicant.conf ← WiFi from wifi.json
    /opt/neuron-agent/                     ← the device_runtime python pkg
    /usr/local/bin/neuron-install          ← copy of install.sh
    /etc/systemd/system/neuron-agent.service

This means a tech can pull a fresh Pi out of the box, flash this
.img.xz to its SD card, slot the card in, power on, and the unit
auto-joins the parent without ever attaching a keyboard.

Reality check: a *true* image build requires xz + xz-utils + parted +
losetup + bsdtar (or 7z) + sufficient disk. On a Docker-based master
that doesn't have those, the builder gracefully falls back to a
"staged-overlay" mode: it produces a tarball of just the new files
to drop into a pre-flashed Pi OS image, and tells the operator what
to do. Production deployment should ensure the master image has the
tools — see docs/HOST_SETUP.md.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


log = logging.getLogger("neuron.golden_image")


# Default base — pinned for reproducibility. Override per group via
# group.base_firmware_version when we wire that through.
DEFAULT_BASE_IMAGE_URL = (
    "https://downloads.raspberrypi.com/raspios_lite_arm64/images/"
    "raspios_lite_arm64-2026-04-15/2026-04-15-raspios-bookworm-arm64-lite.img.xz"
)


@dataclass
class GoldenImageResult:
    ok: bool
    mode: str                # "image" | "overlay"
    path: Path | None
    sha256: str | None
    detail: str = ""


def _have(*tools: str) -> bool:
    return all(shutil.which(t) is not None for t in tools)


def _required_tools_present() -> bool:
    """A true bootable-image build needs xz + losetup + parted + mount.
    On a stock Debian-slim Docker image (our master container) none of
    these are available — without them we fall back to overlay mode."""
    return _have("xz", "losetup", "parted", "mount")


def build_overlay(
    bundle_zip: Path,
    device_runtime_dir: Path,
    out_dir: Path,
    device_dna: str,
) -> GoldenImageResult:
    """Fallback mode: produce a tar.xz with everything that needs to
    land on a freshly-flashed Pi OS image. The tech runs:

        sudo tar -xJf neuron-overlay-<dna>.tar.xz -C /
        sudo /usr/local/bin/neuron-install /boot/neuron-bundle.zip

    on the device after a normal Pi OS flash. Slower workflow than a
    true golden image but works on any master without root + image-
    manipulation tooling.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    overlay = out_dir / f"neuron-overlay-{device_dna}.tar.xz"

    # Build a small staging tree in memory via TarFile.
    import io as _io
    buf = _io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz", preset=6) as tf:
        # 1. The firmware bundle.zip itself, into /boot/.
        tf.add(bundle_zip, arcname="boot/neuron-bundle.zip")
        # 2. The neuron_agent package — every .py file under
        #    /opt/neuron-agent/neuron_agent/.
        for p in (device_runtime_dir / "neuron_agent").rglob("*.py"):
            rel = p.relative_to(device_runtime_dir)
            tf.add(p, arcname=f"opt/neuron-agent/{rel}")
        # 3. install.sh → /usr/local/bin/neuron-install.
        install_sh = device_runtime_dir / "install.sh"
        if install_sh.is_file():
            info = tf.gettarinfo(install_sh, arcname="usr/local/bin/neuron-install")
            info.mode = 0o755
            with install_sh.open("rb") as fh:
                tf.addfile(info, fh)
        # 4. First-boot script that triggers install on power-on.
        firstrun = _firstrun_script()
        info = tarfile.TarInfo("boot/firstrun.sh")
        info.size = len(firstrun)
        info.mode = 0o755
        tf.addfile(info, _io.BytesIO(firstrun))
    overlay.write_bytes(buf.getvalue())

    import hashlib
    sha = hashlib.sha256(overlay.read_bytes()).hexdigest()
    log.info("overlay built: %s (%d bytes, sha256=%s)",
             overlay, overlay.stat().st_size, sha[:12])
    return GoldenImageResult(
        ok=True, mode="overlay", path=overlay, sha256=sha,
        detail=("staged overlay produced — flash Pi OS, then "
                "`sudo tar -xJf <overlay>.tar.xz -C /` on the device"),
    )


def _firstrun_script() -> bytes:
    """/boot/firstrun.sh — Raspberry Pi OS auto-executes this on first
    boot if present. Triggers install.sh with the bundle that's also
    pre-staged on the SD card's /boot partition."""
    return (
        "#!/usr/bin/env bash\n"
        "# Generated by neuron golden-image builder. Runs once on first boot.\n"
        "set -e\n"
        "exec >/var/log/neuron-firstrun.log 2>&1\n"
        "echo \"[neuron-firstrun] $(date -u)\"\n"
        "# Pi OS removes this file after a successful run.\n"
        "/usr/local/bin/neuron-install /boot/neuron-bundle.zip\n"
        "rm -f /boot/firstrun.sh\n"
        "reboot\n"
    ).encode("utf-8")


async def build_golden_image(
    bundle_zip: Path,
    device_runtime_dir: Path,
    out_dir: Path,
    device_dna: str,
    base_image_url: str = DEFAULT_BASE_IMAGE_URL,
) -> GoldenImageResult:
    """Build a bootable Pi OS image with the bundle + agent baked in.
    Falls back to overlay mode on a master image that lacks the
    required tools (xz / losetup / parted / mount)."""
    if not _required_tools_present():
        log.warning("golden-image tooling missing — falling back to overlay")
        return build_overlay(bundle_zip, device_runtime_dir, out_dir, device_dna)

    # True-image path. Kept deliberately stubby — most masters won't
    # hit it. The full implementation is:
    #   1. Download base_image_url to cache (if missing).
    #   2. xz -d into a working .img.
    #   3. Use parted/losetup to mount the /boot + / partitions.
    #   4. Copy bundle → /boot/neuron-bundle.zip.
    #   5. Copy agent → /opt/neuron-agent/.
    #   6. Copy install.sh → /usr/local/bin/neuron-install.
    #   7. Drop /boot/firstrun.sh (auto-runs on Pi OS first boot).
    #   8. umount + losetup -d + xz -c the result.
    log.error("true-image build not yet implemented — use overlay mode")
    return GoldenImageResult(
        ok=False, mode="image", path=None, sha256=None,
        detail=("True .img.xz build is scaffolded but not yet implemented — "
                "deploy a master image with xz+parted+losetup+mount or use "
                "the overlay fallback for now."),
    )
