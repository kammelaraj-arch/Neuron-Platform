#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
#  Install the Neuron vhost watchdog as a systemd timer (root).
#
#    sudo bash /opt/neuron-platform/scripts/install-vhost-watchdog.sh
#
#  What it does:
#    1. Snapshots the *currently good* /opt/shitaleco/nginx/conf.d/
#       neuron.conf to /opt/neuron-platform/host-nginx/neuron.conf
#       (our canonical source of truth). Refuses to do this if the
#       current file does NOT already use the neuron-only cert paths
#       — that means we'd snapshot a broken state.
#    2. Installs:
#         /usr/local/bin/neuron-vhost-watchdog
#         /etc/systemd/system/neuron-vhost-watchdog.service
#         /etc/systemd/system/neuron-vhost-watchdog.timer
#    3. Enables + starts the timer.
#
#  Re-runnable safely; replaces existing units in place. To uninstall:
#    sudo systemctl disable --now neuron-vhost-watchdog.timer
#    sudo rm /etc/systemd/system/neuron-vhost-watchdog.{service,timer} \
#           /usr/local/bin/neuron-vhost-watchdog
#    sudo systemctl daemon-reload
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="${NEURON_INSTALL_DIR:-/opt/neuron-platform}"
CANONICAL="$INSTALL_DIR/host-nginx/neuron.conf"
TARGET="${NEURON_VHOST_TARGET:-/opt/shitaleco/nginx/conf.d/neuron.conf}"
SOURCES="$INSTALL_DIR/scripts"

R="\033[0;31m"; G="\033[0;32m"; Y="\033[1;33m"; B="\033[1m"; N="\033[0m"
ok()   { echo -e "${G}  ✓ $*${N}"; }
err()  { echo -e "${R}  ✗ $*${N}"; }
warn() { echo -e "${Y}  ⚠ $*${N}"; }
step() { echo -e "\n${B}▶ $*${N}"; }

if [ "$(id -u)" -ne 0 ]; then
  err "Run as root."
  exit 1
fi

# ─── 1. Sanity ──────────────────────────────────────────────────────────────
step "[1/4] Sanity"
if [ ! -f "$TARGET" ]; then
  err "$TARGET does not exist. Set up the host vhost first."
  exit 1
fi
if ! grep -q '/etc/letsencrypt/live/neuron.shital.org.uk/' "$TARGET"; then
  err "$TARGET does not reference /etc/letsencrypt/live/neuron.shital.org.uk/."
  err "Run scripts/vps-issue-cert.sh first so the target is in a known-good state,"
  err "then re-run this installer to snapshot it."
  exit 1
fi
ok "$TARGET is in known-good state — safe to snapshot"

# ─── 2. Snapshot to canonical ───────────────────────────────────────────────
step "[2/4] Snapshotting canonical vhost to $CANONICAL"
mkdir -p "$(dirname "$CANONICAL")"
cp "$TARGET" "$CANONICAL"
chown -R neuron:neuron "$(dirname "$CANONICAL")"
chmod 0644 "$CANONICAL"
ok "Canonical saved (owned by neuron:neuron)"

# ─── 3. Install systemd units + watchdog script ─────────────────────────────
step "[3/4] Installing watchdog + systemd units"
install -m 0755 "$SOURCES/vhost-watchdog.sh"                       /usr/local/bin/neuron-vhost-watchdog
install -m 0644 "$SOURCES/systemd/neuron-vhost-watchdog.service"   /etc/systemd/system/
install -m 0644 "$SOURCES/systemd/neuron-vhost-watchdog.timer"     /etc/systemd/system/
ok "Installed"

# ─── 4. Enable + start ──────────────────────────────────────────────────────
step "[4/4] Enabling + starting timer"
systemctl daemon-reload
systemctl enable --now neuron-vhost-watchdog.timer >/dev/null
systemctl status neuron-vhost-watchdog.timer --no-pager --lines=2 || true
echo ""
ok "Timer active. The watchdog will check every 60 s."
echo ""
echo "  Force a check now:       sudo systemctl start neuron-vhost-watchdog.service"
echo "  See activity:            journalctl -u neuron-vhost-watchdog.service -n 20 --no-pager"
echo "  Disable temporarily:     sudo systemctl stop neuron-vhost-watchdog.timer"
echo "  Update canonical:        sudo cp $TARGET $CANONICAL  # after intentionally editing the vhost"
