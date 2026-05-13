#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
#  Neuron Master Platform — Deploy
#  Pipeline end-to-end verified: 2026-05-11 (retry)
#
#  Standalone. Builds and restarts only the Neuron stack defined in
#  master_platform/docker-compose.yml. Does NOT touch any external
#  reverse proxy, nginx, or other service on the host — that is treated
#  as out-of-scope infrastructure and configured once during host setup
#  (see docs/HOST_SETUP.md).
#
#  Usage:
#    bash deploy.sh                  # full deploy (pull → build → up → healthz)
#    bash deploy.sh --no-pull        # skip git pull
#    bash deploy.sh --logs           # tail container logs after deploy
#    bash deploy.sh --branch=NAME    # deploy a non-default branch
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
NEURON_DIR="$SCRIPT_DIR"
COMPOSE="$NEURON_DIR/master_platform/docker-compose.yml"
DEPLOY_BRANCH="${NEURON_DEPLOY_BRANCH:-main}"

DO_PULL=1
LOGS_AFTER=0
for arg in "$@"; do
  case $arg in
    --no-pull)  DO_PULL=0 ;;
    --logs)     LOGS_AFTER=1 ;;
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

step "Neuron Master Platform — deploy"
echo "  compose:        $COMPOSE"
echo "  deploy branch:  $DEPLOY_BRANCH"
echo "  pull:           $([ $DO_PULL -eq 1 ] && echo yes || echo no)"

# ─── 1. Sync repo ────────────────────────────────────────────────────────────
if [ "$DO_PULL" -eq 1 ]; then
  step "[1/4] Syncing repo to $DEPLOY_BRANCH"
  cd "$NEURON_DIR"
  if [ -d .git ]; then
    git fetch origin "$DEPLOY_BRANCH" --quiet
    git checkout -B "$DEPLOY_BRANCH" "origin/$DEPLOY_BRANCH" --quiet
    git reset --hard "origin/$DEPLOY_BRANCH" --quiet
    ok "Updated to $(git rev-parse --short HEAD) ($DEPLOY_BRANCH)"
  else
    warn "Not a git checkout — skipping pull"
  fi
fi

if [ ! -d "$NEURON_DIR/master_platform" ]; then
  err "Expected $NEURON_DIR/master_platform to exist after sync."
  exit 1
fi

# cd so docker compose auto-picks up master_platform/.env (NEURON_DEPLOY_SECRET,
# NEURON_DEPLOY_BRANCH, NEURON_REPO_PATH, optional port/container overrides).
# The compose project name is intentionally NOT overridden — it defaults to
# the directory basename ("master_platform"), which matches the project the
# running stack was first created in. Forcing a different project name here
# would make compose blind to the existing containers and fail with name
# conflicts on docker compose up.
cd "$NEURON_DIR/master_platform"

# ─── 2. Build ────────────────────────────────────────────────────────────────
step "[2/4] Building neuron-master image"
# Stamp the running container with the deployed commit + build time so
# /api/version and /healthz can answer "what's running?" in one curl.
GIT_SHA="$(git -C "$NEURON_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  git sha:        $GIT_SHA"
echo "  build time:     $BUILD_TIME"
NEURON_GIT_SHA="$GIT_SHA" NEURON_BUILD_TIME="$BUILD_TIME" \
  docker compose -f "$COMPOSE" build neuron-master \
    --build-arg "NEURON_GIT_SHA=$GIT_SHA" \
    --build-arg "NEURON_BUILD_TIME=$BUILD_TIME"
ok "Image built ($GIT_SHA / $BUILD_TIME)"

# ─── 3. Up — zero-downtime swap when possible ────────────────────────────────
step "[3/4] Starting neuron-master (graceful recreate)"
# Image is already built in step 2 — the swap window is just stop + start of
# the new container against the cached image, typically 2-5s. The host
# nginx vhost (host-nginx/neuron.conf) has proxy_next_upstream + retry
# configured so connection-refused during this window is absorbed
# rather than surfaced as a 502 to the client.
#
# If neuron-master isn't currently up (cold deploy), --no-deps still
# brings it up clean.
if docker ps --format '{{.Names}}' | grep -qx "${NEURON_MASTER_CONTAINER:-neuron-master}"; then
  echo "  (graceful recreate — pre-built image, host nginx absorbs the gap)"
fi
docker compose -f "$COMPOSE" up -d --no-deps --force-recreate neuron-master
ok "Container started"

# ─── 4. Health check ─────────────────────────────────────────────────────────
step "[4/4] Waiting for /healthz"
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
  echo " then sign in and DELETE the file:"
  echo ""
  echo "   docker compose -f $COMPOSE exec neuron-master \\"
  echo "     rm master_platform/data/bootstrap_admin.txt"
  echo "─────────────────────────────────────────────────────────────"
fi

if [ "$LOGS_AFTER" -eq 1 ]; then
  docker compose -f "$COMPOSE" logs -f neuron-master
fi

echo ""
echo -e "${B}✅ Deploy complete${N}"
