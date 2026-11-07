#!/usr/bin/env bash
# Uniroom: fresh scrape + Cloudflare Pages deploy.
# Called once a day by cron / systemd, but safe to run by hand too.
set -euo pipefail

# --- configure these ---------------------------------------------------------
REPO_DIR="/opt/uniroom"        # where this repo is cloned on the Debian box
PROJECT_NAME="uniroom"         # your Cloudflare Pages project name
# -----------------------------------------------------------------------------

cd "$REPO_DIR"
mkdir -p logs
LOG="$REPO_DIR/logs/daily.log"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# Load Cloudflare secrets (CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID) from .env
if [ -f "$REPO_DIR/.env" ]; then
  set -a; . "$REPO_DIR/.env"; set +a
fi

log "=== daily run start ==="

log "scraping fresh data (--fresh)..."
python3 build_data.py --fresh

log "deploying site/ to Cloudflare Pages ($PROJECT_NAME)..."
wrangler pages deploy "$REPO_DIR/site" --project-name "$PROJECT_NAME"

log "=== daily run done ==="