#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
#  Neuron Master Platform — Independent Deploy
#
#  This script ONLY touches the Neuron stack. It never restarts, pulls,
#  or otherwise mutates any ShitalEco service. The only "shared" action
#  is a SIGHUP to the existing nginx (graceful reload, no downtime, no
#  restart) — and only when nginx is actually present on this host.
#
#  Usage:
#    bash neuron-platform/deploy.sh                 # full deploy
#    bash neuron-platform/deploy.sh --no-pull       # skip git pull
#    bash neuron-platform/deploy.sh --no-nginx      # skip nginx reload
#    bash neuron-platform/deploy.sh --logs          # tail container logs
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# Resolve script dir → neuron-platform absolute path.
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
NEURON_DIR="$SCRIPT_DIR"
COMPOSE="$NEURON_DIR/master_platform/docker-compose.yml"

# Canonical deploy branch. Override with --branch=... or env var if testing.
DEPLOY_BRANCH="${NEURON_DEPLOY_BRANCH:-claude/shital-erp-platform-iR2UF}"

# nginx is shared. We only ask it to reload its config; we never restart
# its container or touch its compose. Override with --nginx-compose to
# point elsewhere, or --no-nginx to skip the reload entirely.
NGINX_COMPOSE="${NGINX_COMPOSE:-/opt/shitaleco/docker-compose.prod.yml}"
NGINX_SERVICE="${NGINX_SERVICE:-nginx}"

DO_PULL=1
DO_NGINX=1
LOGS_AFTER=0
for arg in "$@"; do
  case $arg in
    --no-pull)  DO_PULL=0 ;;
    --no-nginx) DO_NGINX=0 ;;
    --logs)     LOGS_AFTER=1 ;;
    --nginx-compose=*) NGINX_COMPOSE="${arg#*=}" ;;
    --branch=*) DEPLOY_BRANCH="${arg#*=}" ;;
    -h|--help)
      sed -n '2,16p' "$0"; exit 0 ;;
  esac
done

R="\033[0;31m"; G="\033[0;32m"; Y="\033[1;33m"; B="\033[1m"; N="\033[0m"
ok()   { echo -e "${G}  ✓ $*${N}"; }
warn() { echo -e "${Y}  ⚠ $*${N}"; }
err()  { echo -e "${R}  ✗ $*${N}"; }
step() { echo -e "\n${B}▶ $*${N}"; }

step "Neuron Master Platform — independent deploy"
echo "  compose:        $COMPOSE"
echo "  deploy branch:  $DEPLOY_BRANCH"
echo "  nginx compose:  $NGINX_COMPOSE (reload only, never restart)"
echo "  pull:           $([ $DO_PULL -eq 1 ] && echo yes || echo no)"
echo "  nginx reload:   $([ $DO_NGINX -eq 1 ] && echo yes || echo no)"

# ─── 1. Sync repo (only if asked) ────────────────────────────────────────────
# Pinned to DEPLOY_BRANCH — never inferred from `git rev-parse HEAD`, since
# the operator may be on a feature branch when they run this.
if [ "$DO_PULL" -eq 1 ]; then
  step "[1/5] Syncing repo to $DEPLOY_BRANCH"
  REPO_ROOT="$( cd -- "$NEURON_DIR/.." &> /dev/null && pwd )"
  cd "$REPO_ROOT"
  if [ -d .git ]; then
    git fetch origin "$DEPLOY_BRANCH" --quiet
    git checkout -B "$DEPLOY_BRANCH" "origin/$DEPLOY_BRANCH" --quiet
    git reset --hard "origin/$DEPLOY_BRANCH" --quiet
    ok "Updated to $(git rev-parse --short HEAD) ($DEPLOY_BRANCH)"
  else
    warn "Not a git checkout — skipping pull"
  fi
fi

# Re-resolve in case the pull above moved files in/out of NEURON_DIR.
if [ ! -d "$NEURON_DIR/master_platform" ]; then
  err "Expected $NEURON_DIR/master_platform to exist after sync."
  err "Either DEPLOY_BRANCH=$DEPLOY_BRANCH doesn't have neuron-platform/ yet,"
  err "or the checkout is in an unexpected state."
  exit 1
fi

# ─── 2. Build the Neuron image ───────────────────────────────────────────────
step "[2/5] Building neuron-master image"
cd "$NEURON_DIR/master_platform"
docker compose -f "$COMPOSE" build neuron-master
ok "Image built"

# ─── 3. Up only neuron-master (its own compose, own network, own volumes) ──
step "[3/5] Starting neuron-master"
docker compose -f "$COMPOSE" up -d --no-deps neuron-master
ok "Container started"

# ─── 4. Health check ────────────────────────────────────────────────────────
step "[4/5] Waiting for /healthz"
for i in $(seq 1 24); do
  if docker compose -f "$COMPOSE" exec -T neuron-master \
      python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/healthz').status==200 else 1)" \
      > /dev/null 2>&1; then
    ok "neuron-master healthy (${i}×2s)"
    HEALTHY=1; break
  fi
  printf "."
  sleep 2
done
echo ""
if [ -z "${HEALTHY:-}" ]; then
  err "neuron-master did NOT become healthy in 48 s"
  echo ""
  warn "Last 30 lines of container logs:"
  docker compose -f "$COMPOSE" logs --tail=30 neuron-master || true
  exit 1
fi

# ─── 5. Re-stage + reload the shared nginx (no restart, no downtime) ────────
# The vhost file canonical copy lives inside neuron-platform/nginx/neuron.conf
# and we copy it to the host's nginx conf.d on every deploy. This way other
# automation that mutates /opt/shitaleco (e.g. the deployer container's git
# hook) can't permanently break the Neuron vhost — re-run this script and
# the vhost is back.
NGINX_CONF_HOST_DIR="${NGINX_CONF_HOST_DIR:-/opt/shitaleco/nginx/conf.d}"
SOURCE_VHOST="$NEURON_DIR/nginx/neuron.conf"

if [ "$DO_NGINX" -eq 1 ]; then
  step "[5/5] Re-staging neuron.conf and reloading shared nginx"
  if [ -f "$SOURCE_VHOST" ] && [ -d "$NGINX_CONF_HOST_DIR" ]; then
    if ! cmp -s "$SOURCE_VHOST" "$NGINX_CONF_HOST_DIR/neuron.conf" 2>/dev/null; then
      cp "$SOURCE_VHOST" "$NGINX_CONF_HOST_DIR/neuron.conf"
      ok "Re-staged $NGINX_CONF_HOST_DIR/neuron.conf from $SOURCE_VHOST"
    else
      ok "neuron.conf already up to date in $NGINX_CONF_HOST_DIR"
    fi
  elif [ ! -f "$SOURCE_VHOST" ]; then
    warn "Source vhost missing: $SOURCE_VHOST"
  else
    warn "Nginx conf dir missing: $NGINX_CONF_HOST_DIR"
  fi
  if docker compose -f "$NGINX_COMPOSE" exec -T "$NGINX_SERVICE" nginx -t >/dev/null 2>&1; then
    if docker compose -f "$NGINX_COMPOSE" exec -T "$NGINX_SERVICE" nginx -s reload >/dev/null 2>&1; then
      ok "Nginx config reloaded"
    else
      warn "nginx -t passed but reload failed; reload manually: docker compose -f $NGINX_COMPOSE exec $NGINX_SERVICE nginx -s reload"
    fi
  else
    err "nginx -t FAILED — refusing to reload. Run manually to see the error:"
    err "  docker compose -f $NGINX_COMPOSE exec $NGINX_SERVICE nginx -t"
  fi
else
  step "[5/5] Skipping nginx reload (--no-nginx)"
fi

# ─── First-time bootstrap key hint ──────────────────────────────────────────
if docker compose -f "$COMPOSE" exec -T neuron-master \
   test -f master_platform/data/bootstrap_admin.txt 2>/dev/null; then
  echo ""
  echo "─────────────────────────────────────────────────────────────"
  echo " First-time setup detected. Copy the bootstrap admin key:"
  echo ""
  echo "   docker compose -f $COMPOSE exec neuron-master \\"
  echo "     cat master_platform/data/bootstrap_admin.txt"
  echo ""
  echo " then sign in at https://neuron.shital.org.uk/login and DELETE"
  echo " the file:"
  echo ""
  echo "   docker compose -f $COMPOSE exec neuron-master \\"
  echo "     rm master_platform/data/bootstrap_admin.txt"
  echo "─────────────────────────────────────────────────────────────"
fi

if [ "$LOGS_AFTER" -eq 1 ]; then
  docker compose -f "$COMPOSE" logs -f neuron-master
fi

echo ""
echo -e "${B}✅ Deploy complete${N} — https://neuron.shital.org.uk"
