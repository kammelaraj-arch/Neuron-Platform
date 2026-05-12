#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
#  Neuron Platform — client-side scan helper.
#
#  Run this on YOUR LAPTOP / DESKTOP (the device you're physically using
#  to access the platform UI). It scans the WiFi networks + LAN devices
#  visible from your machine and uploads them to the device-registration
#  wizard at the given group URL.
#
#  Why: browsers can't access WiFi or do ARP sweeps for security reasons.
#  Running this helper from your terminal gives the wizard a "client-side"
#  view of the local network, distinct from the "edge-side" view.
#
#  USAGE (copy-paste into a terminal — replace the URL with the one shown
#  in the wizard's "Client scan" panel):
#
#    bash <(curl -fsSL https://neuron.shital.org.uk/static/neuron-client-scan.sh) \
#      --group https://neuron.shital.org.uk/api/wizard/<GROUP_ID>/client-scan \
#      --secret <X-API-KEY>
#
#  Supported hosts: Linux (nmcli/iwlist/nmap), macOS (airport/arp), Windows
#  (netsh wlan / arp -a) under WSL or Git-Bash.
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

GROUP_URL=""
API_KEY=""
SESSION_COOKIE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --group)    GROUP_URL="$2"; shift 2 ;;
    --secret)   API_KEY="$2"; shift 2 ;;
    --cookie)   SESSION_COOKIE="$2"; shift 2 ;;
    -h|--help)  sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

if [ -z "$GROUP_URL" ]; then
  echo "ERROR: --group <url> is required (copy from the wizard's Client-scan panel)" >&2
  exit 1
fi
if [ -z "$API_KEY" ] && [ -z "$SESSION_COOKIE" ]; then
  echo "ERROR: provide --secret <API_KEY> or --cookie <session-cookie-value>" >&2
  exit 1
fi

OS="$(uname -s)"
HOST="$(hostname)"

# ─── WiFi scan ──────────────────────────────────────────────────────────────
wifi_json="[]"
if command -v nmcli >/dev/null 2>&1; then
  # Linux with NetworkManager
  nmcli dev wifi rescan >/dev/null 2>&1 || true
  wifi_json=$(nmcli -t -e no -f SSID,SIGNAL,SECURITY,FREQ dev wifi | \
    awk -F: 'BEGIN{print "["} NR>1{print ","} length($1){
      gsub(/\\:/, ":", $1); printf "  {\"ssid\":\"%s\",\"signal\":%s,\"security\":\"%s\",\"frequency_mhz\":%s}", $1, ($2?$2:"0"), ($3?tolower($3):"open"), ($4?$4:"null")
    } END{print "]"}' | tr '\n' ' ')
elif [ "$OS" = "Darwin" ]; then
  # macOS
  airport="/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
  if [ -x "$airport" ]; then
    wifi_json=$("$airport" -s | awk 'NR>1{
      gsub(/^[ \t]+|[ \t]+$/, "", $0)
      n = split($0, a, /[ \t][ \t]+/);
      if (n >= 4) printf "%s{\"ssid\":\"%s\",\"signal\":%s,\"security\":\"%s\"}", (NR==2?"":","), a[1], a[3], tolower(a[7])
    } BEGIN{printf "["} END{print "]"}')
  fi
elif command -v iwlist >/dev/null 2>&1; then
  IF=$(iw dev | awk '/Interface/{print $2; exit}')
  raw=$(sudo iwlist "${IF:-wlan0}" scan 2>/dev/null || iwlist "${IF:-wlan0}" scan 2>/dev/null)
  wifi_json=$(echo "$raw" | awk '
    BEGIN{first=1; printf "["}
    /Cell /{if(!first){printf "}"} else first=0; printf "%s{", (first?"":",")}
    /ESSID:/{gsub(/.*ESSID:|"|$/,"",$0); printf "\"ssid\":\"%s\"", $0}
    /Signal level=/{m=match($0,/Signal level=(-?[0-9]+)/,a); if(m){printf ",\"signal\":%s", a[1]}}
    END{if(!first){printf "}"} ; printf "]"}')
fi

# ─── Device scan (LAN) ──────────────────────────────────────────────────────
dev_json="[]"
if command -v nmap >/dev/null 2>&1; then
  subnet=$(ip route 2>/dev/null | awk '/proto kernel/{print $1; exit}')
  if [ -z "$subnet" ]; then subnet="192.168.1.0/24"; fi
  dev_json=$(nmap -sn -PR -T4 -oG - "$subnet" 2>/dev/null | awk '
    BEGIN{first=1; printf "["}
    /^Host:/{
      ip=$2; host=""
      if (match($0,/\(([^)]*)\)/,a)) host=a[1]
      if (/Status: Up/){
        printf "%s{\"ip\":\"%s\",\"hostname\":\"%s\"}", (first?"":","), ip, host; first=0
      }
    }
    END{printf "]"}')
elif [ -x /usr/sbin/arp ] || command -v arp >/dev/null 2>&1; then
  dev_json=$(arp -a 2>/dev/null | awk '
    BEGIN{first=1; printf "["}
    /\(/{
      ip=""; mac=""
      if (match($0, /\(([0-9.]+)\)/, a)) ip=a[1]
      if (match($0, /([0-9a-fA-F:]{17})/, b)) mac=b[1]
      if (ip != "") {
        printf "%s{\"ip\":\"%s\",\"mac\":\"%s\"}", (first?"":","), ip, mac; first=0
      }
    }
    END{printf "]"}')
fi

# ─── POST ───────────────────────────────────────────────────────────────────
body="{\"wifi\":${wifi_json},\"devices\":${dev_json},\"host_hint\":\"$HOST\"}"
echo "Posting scan to $GROUP_URL …"
if [ -n "$API_KEY" ]; then
  curl -fsS -X POST -H "Content-Type: application/json" \
    -H "X-API-Key: $API_KEY" -d "$body" "$GROUP_URL"
else
  curl -fsS -X POST -H "Content-Type: application/json" \
    -H "Cookie: $SESSION_COOKIE" -d "$body" "$GROUP_URL"
fi
echo ""
echo "✓ Posted. Refresh the wizard to see results."
