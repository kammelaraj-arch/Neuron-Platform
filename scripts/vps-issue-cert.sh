#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
#  Neuron Platform — issue an independent Let's Encrypt cert for
#  neuron.shital.org.uk.
#
#  Run this on the production VPS, AS root (it needs to install certbot
#  on the host and write to /etc/letsencrypt/).
#
#    sudo bash /opt/neuron-platform/scripts/vps-issue-cert.sh
#
#  What it does:
#    1. Installs certbot if not present (apt-get).
#    2. Issues a SINGLE-DOMAIN cert for neuron.shital.org.uk via
#       webroot, using ShitalEco's existing ACME challenge directory
#       (/var/www/certbot — already served by shitaleco-nginx-1 on
#       /.well-known/acme-challenge/).
#    3. Edits ONE line in /opt/shitaleco/nginx/conf.d/neuron.conf to
#       swap the ssl_certificate paths from shital.org.uk →
#       neuron.shital.org.uk. Backup saved alongside.
#    4. Validates the new nginx config inside shitaleco-nginx-1.
#    5. Reloads (SIGHUP, no restart) shitaleco-nginx-1.
#    6. Installs a certbot deploy-hook that re-runs the nginx reload on
#       every future renewal.
#
#  What this DOES NOT do to ShitalEco:
#    - Does NOT change any ShitalEco container image, env, or volume.
#    - Does NOT touch ShitalEco's certbot container or its renewal cron.
#    - Does NOT modify any file inside /opt/shitaleco/ except the one
#      Neuron vhost (which has always been a Neuron-managed file living
#      in a shared directory).
#    - Does NOT restart ShitalEco's nginx container (only SIGHUP'd).
#
#  Idempotent — safe to re-run.
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

DOMAIN="${NEURON_CERT_DOMAIN:-neuron.shital.org.uk}"
EMAIL="${NEURON_CERT_EMAIL:-}"
WEBROOT="${NEURON_CERT_WEBROOT:-/var/www/certbot}"
VHOST="${NEURON_VHOST_PATH:-/opt/shitaleco/nginx/conf.d/neuron.conf}"
NGINX_CONTAINER="${NEURON_NGINX_CONTAINER:-shitaleco-nginx-1}"

R="\033[0;31m"; G="\033[0;32m"; Y="\033[1;33m"; B="\033[1m"; N="\033[0m"
ok()   { echo -e "${G}  ✓ $*${N}"; }
warn() { echo -e "${Y}  ⚠ $*${N}"; }
err()  { echo -e "${R}  ✗ $*${N}"; }
step() { echo -e "\n${B}▶ $*${N}"; }

if [ "$(id -u)" -ne 0 ]; then
  err "Run as root (the script edits /etc/letsencrypt and the host nginx vhost file)."
  err "Try: sudo bash $0"
  exit 1
fi

# ─── 1. Sanity checks before changing anything ───────────────────────────────
step "[1/7] Pre-flight checks"

if [ ! -d "$WEBROOT" ]; then
  err "Webroot $WEBROOT not found. Cannot run ACME http-01 challenge."
  err "Override with NEURON_CERT_WEBROOT=/path/to/webroot"
  exit 1
fi
ok "Webroot $WEBROOT exists"

if ! docker ps --format '{{.Names}}' | grep -qx "$NGINX_CONTAINER"; then
  err "Container $NGINX_CONTAINER is not running."
  err "Override with NEURON_NGINX_CONTAINER=<name> if your host nginx has a different name."
  exit 1
fi
ok "$NGINX_CONTAINER is running"

if [ ! -f "$VHOST" ]; then
  err "Vhost file $VHOST not found. Cannot patch cert paths."
  exit 1
fi
ok "Vhost file present: $VHOST"

# Confirm the vhost references the old cert path so we know what to swap.
if ! grep -q '/etc/letsencrypt/live/shital.org.uk/' "$VHOST"; then
  warn "Vhost does not reference /etc/letsencrypt/live/shital.org.uk/"
  warn "It may already be using neuron.shital.org.uk's cert, or a custom path."
  warn "Current ssl_certificate lines:"
  grep -n 'ssl_certificate' "$VHOST" | sed 's/^/    /'
fi

# Confirm the http-01 challenge would actually reach this webroot from the
# public internet. We can't 100% prove this without an external request, but
# we can confirm the nginx container has a route to it.
NGINX_WEBROOT_MOUNT=$(docker inspect "$NGINX_CONTAINER" \
  --format '{{range .Mounts}}{{if eq .Destination "/var/www/certbot"}}{{.Source}}{{end}}{{end}}' \
  2>/dev/null || true)
if [ -z "$NGINX_WEBROOT_MOUNT" ]; then
  warn "$NGINX_CONTAINER does not have /var/www/certbot bind-mounted from host."
  warn "ACME http-01 challenge may not be reachable from Let's Encrypt."
  warn "Continuing anyway — the certbot step will fail loudly if so."
else
  ok "$NGINX_CONTAINER serves /var/www/certbot from host:$NGINX_WEBROOT_MOUNT"
fi

# ─── 2. Install certbot if needed ────────────────────────────────────────────
step "[2/7] Certbot on host"
if ! command -v certbot >/dev/null 2>&1; then
  warn "certbot not installed; installing via apt"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot
  ok "Installed $(certbot --version)"
else
  ok "Already installed: $(certbot --version)"
fi

# ─── 3. Email for Let's Encrypt notices ──────────────────────────────────────
if [ -z "$EMAIL" ]; then
  step "[3/7] Let's Encrypt account email"
  echo "  certbot needs an email for expiry/renewal notices."
  echo "  Set NEURON_CERT_EMAIL=you@example.com to suppress this prompt next time."
  read -r -p "  Email: " EMAIL
  if [ -z "$EMAIL" ]; then
    err "Email is required."
    exit 1
  fi
fi

# ─── 4. Issue the cert (or report existing) ──────────────────────────────────
step "[4/7] Issuing cert for $DOMAIN"
LIVE_DIR="/etc/letsencrypt/live/$DOMAIN"
if [ -f "$LIVE_DIR/fullchain.pem" ] && [ -f "$LIVE_DIR/privkey.pem" ]; then
  ok "Existing cert at $LIVE_DIR — skipping issuance"
  certbot certificates --cert-name "$DOMAIN" 2>/dev/null | sed 's/^/    /' || true
else
  certbot certonly \
    --webroot -w "$WEBROOT" \
    -d "$DOMAIN" \
    --non-interactive --agree-tos \
    --email "$EMAIL" \
    --cert-name "$DOMAIN"
  ok "Cert issued at $LIVE_DIR"
fi

if [ ! -f "$LIVE_DIR/fullchain.pem" ] || [ ! -f "$LIVE_DIR/privkey.pem" ]; then
  err "Expected cert files not present at $LIVE_DIR after issuance."
  exit 1
fi

# ─── 5. Patch the vhost cert paths ───────────────────────────────────────────
step "[5/7] Patching $VHOST"
BACKUP="$VHOST.bak.$(date +%s)"
cp "$VHOST" "$BACKUP"
ok "Backup saved at $BACKUP"

# Replace any cert path under live/shital.org.uk/ with live/<domain>/.
# Idempotent — safe to re-run.
sed -i \
  -e "s|/etc/letsencrypt/live/shital.org.uk/fullchain.pem|/etc/letsencrypt/live/$DOMAIN/fullchain.pem|g" \
  -e "s|/etc/letsencrypt/live/shital.org.uk/privkey.pem|/etc/letsencrypt/live/$DOMAIN/privkey.pem|g" \
  "$VHOST"

# Show resulting cert lines
echo "  Resulting ssl_certificate config:"
grep -n 'ssl_certificate' "$VHOST" | sed 's/^/    /'

# ─── 6. Validate + reload nginx ──────────────────────────────────────────────
step "[6/7] Validating + reloading $NGINX_CONTAINER"
if ! docker exec "$NGINX_CONTAINER" nginx -t >/dev/null 2>&1; then
  err "nginx -t FAILED. Rolling back $VHOST."
  cp "$BACKUP" "$VHOST"
  docker exec "$NGINX_CONTAINER" nginx -t || true
  err "Vhost restored. No reload performed. Inspect the test output above."
  exit 1
fi
ok "nginx -t passed"

docker exec "$NGINX_CONTAINER" nginx -s reload
ok "$NGINX_CONTAINER reloaded (SIGHUP, no service restart)"

# ─── 7. Renewal deploy-hook ──────────────────────────────────────────────────
step "[7/7] Installing renewal deploy-hook"
HOOK_DIR="/etc/letsencrypt/renewal-hooks/deploy"
HOOK_PATH="$HOOK_DIR/neuron-reload-host-nginx.sh"
mkdir -p "$HOOK_DIR"
cat > "$HOOK_PATH" <<EOF
#!/bin/sh
# Auto-installed by /opt/neuron-platform/scripts/vps-issue-cert.sh.
# Fires after every certbot renewal of $DOMAIN to reload host nginx.
case "\$RENEWED_DOMAINS" in
  *$DOMAIN*)
    docker exec $NGINX_CONTAINER nginx -t \\
      && docker exec $NGINX_CONTAINER nginx -s reload
    ;;
esac
EOF
chmod 0755 "$HOOK_PATH"
ok "Hook installed at $HOOK_PATH"

# ─── Verification ────────────────────────────────────────────────────────────
echo ""
step "Done. Verifying end-to-end"
SERVED=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName 2>/dev/null \
  | tr '\n' ' ')
echo "  Cert served for $DOMAIN:"
echo "    $SERVED"
echo ""
if echo "$SERVED" | grep -q "DNS:$DOMAIN"; then
  ok "✅ Cert now correctly serves $DOMAIN as a SAN."
else
  warn "Cert SAN list does not yet include $DOMAIN."
  warn "If you just reloaded, give it 5 s and re-check with:"
  warn "  echo | openssl s_client -servername $DOMAIN -connect $DOMAIN:443 2>/dev/null \\"
  warn "    | openssl x509 -noout -subject -ext subjectAltName"
fi

echo ""
echo "  Smoke-test the full pipeline (no -k needed if cert is good):"
echo "    curl -fsS https://$DOMAIN/healthz"
echo ""
echo "  Renewal:"
echo "    certbot renew --dry-run    # simulate"
echo "    certbot renew              # real (idempotent)"
