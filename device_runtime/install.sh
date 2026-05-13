#!/usr/bin/env bash
# Neuron device first-boot installer. Runs on the Pi as root.
# Idempotent — safe to re-run for OTA / config updates.
#
# Usage (executed by the master's deploy worker over SSH):
#     sudo bash install.sh <bundle.zip>
#
# Steps:
#   1. Unpack <bundle.zip> into /boot/neuron/.
#   2. Apply WiFi config from wifi.json (wpa_supplicant.conf).
#   3. Apply nftables deny-all firewall from brain.json["network_policy"].
#   4. Disable default `pi` user / require password (idempotent).
#   5. Install neuron-agent.service systemd unit + start it.
#   6. Probe /healthz on localhost — fail loudly if the agent didn't start.

set -euo pipefail

BUNDLE_ZIP="${1:-}"
TARGET=/boot/neuron
AGENT_DIR=/opt/neuron-agent
LOG=/var/log/neuron-install.log

exec > >(tee -a "$LOG") 2>&1
echo "=== neuron install $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if [[ -z "$BUNDLE_ZIP" || ! -f "$BUNDLE_ZIP" ]]; then
    echo "::error::usage: sudo bash install.sh <bundle.zip>" >&2
    exit 1
fi

# 1. Unpack.
mkdir -p "$TARGET"
unzip -o "$BUNDLE_ZIP" -d "$TARGET"
# Lock down private keys — ZIP doesn't reliably preserve unix perms.
if [[ -d "$TARGET/certs" ]]; then
    chmod 700 "$TARGET/certs"
    find "$TARGET/certs" -name '*.key' -exec chmod 600 {} \;
    find "$TARGET/certs" -name '*.crt' -exec chmod 644 {} \;
    echo "✓ cert permissions locked (key 600 / crt 644)"
fi
ls -la "$TARGET"

# 2. WiFi (if a primary network is configured).
PRIMARY_SSID=$(python3 -c "
import json, sys
try:
    d = json.load(open('$TARGET/wifi.json'))
    p = d.get('primary') or {}
    print(p.get('ssid') or '')
except Exception:
    print('')
" || true)
if [[ -n "$PRIMARY_SSID" ]]; then
    PRIMARY_PASS=$(python3 -c "
import json
d = json.load(open('$TARGET/wifi.json'))
print((d.get('primary') or {}).get('password') or '')
")
    wpa_passphrase "$PRIMARY_SSID" "$PRIMARY_PASS" >> /etc/wpa_supplicant/wpa_supplicant.conf
    echo "✓ WiFi primary configured: $PRIMARY_SSID"
fi

# 3. Firewall — the agent reapplies on every start, but doing it here
# means the device is locked down BEFORE the agent even runs.
if [[ -f "$TARGET/brain.json" ]]; then
    INBOUND=$(python3 -c "
import json
try:
    d = json.load(open('$TARGET/brain.json'))
    print((d.get('network_policy') or {}).get('inbound_policy') or '')
except Exception:
    print('')
")
    if [[ "$INBOUND" == "deny" ]]; then
        mkdir -p /etc/nftables.d
        cat > /etc/nftables.d/neuron.nft <<'NFT'
flush ruleset
table inet neuron-policy {
    chain input {
        type filter hook input priority 0; policy drop;
        iif "lo" accept
        ct state { established, related } accept
        ip protocol icmp icmp type { echo-request, destination-unreachable } accept
        ip6 nexthdr ipv6-icmp accept
    }
    chain forward { type filter hook forward priority 0; policy drop; }
    chain output { type filter hook output priority 0; policy accept; }
}
NFT
        nft -f /etc/nftables.d/neuron.nft
        echo "✓ nftables deny-all applied"
    fi
fi

# 4. Disable default pi user — best-effort, no failure if it's already locked.
if id pi >/dev/null 2>&1; then
    passwd -l pi || true
    echo "✓ default pi user locked"
fi

# Lock SSH at boot — parent re-enables on demand via reverse tunnel.
systemctl disable --now ssh.service 2>/dev/null || true
echo "✓ ssh.service disabled at boot"

# 5. Install agent + service unit.
mkdir -p "$AGENT_DIR"
# The agent package lives under /opt/neuron-agent/ — bundles either
# ship it pre-installed (golden image), or we pip-install it on the
# fly. For now we assume the image already has it, fail loud otherwise.
if ! python3 -c "import neuron_agent" 2>/dev/null; then
    echo "::error::python3 -c 'import neuron_agent' failed — agent package not installed on device" >&2
    exit 2
fi

cat > /etc/systemd/system/neuron-agent.service <<'UNIT'
[Unit]
Description=Neuron device agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m neuron_agent --bundle /boot/neuron
Restart=on-failure
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now neuron-agent.service
echo "✓ neuron-agent.service started"

# 6. Probe agent liveness — agent doesn't bind a port (parent-only rule)
# so we check via systemd + journalctl tail.
sleep 5
if ! systemctl is-active --quiet neuron-agent.service; then
    echo "::error::neuron-agent.service failed to start" >&2
    journalctl -u neuron-agent.service --no-pager -n 30
    exit 3
fi
echo "✓ neuron-agent active"
echo "=== neuron install complete ==="
