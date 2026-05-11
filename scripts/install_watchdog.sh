#!/bin/bash
# One-shot installer — run once on the VPS to wire the vhost watchdog.
#   sudo bash /opt/shitaleco/neuron-platform/scripts/install_watchdog.sh
#
# After this, /opt/shitaleco/nginx/conf.d/neuron.conf is auto-restored
# within 60 s of any deletion, with nginx reloaded.
#
# Re-runnable safely; replaces existing units in place.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

install -m 0755 "$SCRIPT_DIR/restage_vhost.sh" /usr/local/bin/neuron-vhost-watchdog
install -m 0644 "$SCRIPT_DIR/neuron-vhost-watchdog.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/neuron-vhost-watchdog.timer"   /etc/systemd/system/

# Point the unit at the installed script (so a future repo wipe
# can't break the systemd unit).
sed -i 's|^ExecStart=.*|ExecStart=/usr/local/bin/neuron-vhost-watchdog|' \
    /etc/systemd/system/neuron-vhost-watchdog.service

systemctl daemon-reload
systemctl enable --now neuron-vhost-watchdog.timer

echo
echo "✓ Installed. Status:"
systemctl status neuron-vhost-watchdog.timer --no-pager --lines=2 || true
echo
echo "Logs:    journalctl -u neuron-vhost-watchdog.service -n 20 --no-pager"
echo "Disable: systemctl disable --now neuron-vhost-watchdog.timer"
