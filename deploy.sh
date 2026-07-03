#!/usr/bin/env bash
set -euo pipefail

# Stone Techno Companion — deploy script
# Deploys server code to VPS with timestamped backup + local copy
# Usage: ./deploy.sh [--dry-run]

cd "$(dirname "$0")"

VPS="root@209.38.244.136"
VPS_DIR="/root/services/stone-techno"
LOCAL_BACKUPS="backups"
LOCAL_ENV="server/.env"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DRY_RUN=false

if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

run() {
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY RUN] $*"
    else
        "$@"
    fi
}

# Vars to sync to production (exclude dev-only: CHAT_BASE_URL, CHAT_ADMIN_TOKEN)
PROD_VARS="OPENAI_API_KEY MAILEROO_API_KEY VAPID_PRIVATE_KEY VAPID_PUBLIC_KEY VAPID_CLAIMS_EMAIL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET CHAT_ADMIN_EMAILS"

if [ "$DRY_RUN" = true ]; then
    echo "=== Stone Techno Deploy (DRY RUN) ==="
else
    echo "=== Stone Techno Deploy ==="
fi
echo ""

# --- Step 0: Sync env vars to VPS ---
echo "[0/6] Syncing env vars to VPS..."
if [ ! -f "$LOCAL_ENV" ]; then
    echo "  ERROR: $LOCAL_ENV not found"
    exit 1
fi

missing=""
for var in $PROD_VARS; do
    val=$(grep "^${var}=" "$LOCAL_ENV" | cut -d= -f2-)
    if [ -z "$val" ]; then
        missing="$missing $var"
    fi
done
if [ -n "$missing" ]; then
    echo "  ERROR: Missing values in $LOCAL_ENV:$missing"
    exit 1
fi

# Build production .env (no CHAT_BASE_URL, no CHAT_ADMIN_TOKEN)
PROD_ENV=""
for var in $PROD_VARS; do
    val=$(grep "^${var}=" "$LOCAL_ENV" | cut -d= -f2-)
    # VAPID_PRIVATE_KEY uses Docker container path in production
    if [ "$var" = "VAPID_PRIVATE_KEY" ]; then
        val="/app/data/vapid_private.pem"
    fi
    PROD_ENV="${PROD_ENV}${var}=${val}\n"
done
# Add CHAT_EVENT_ID if present
eid=$(grep "^CHAT_EVENT_ID=" "$LOCAL_ENV" 2>/dev/null | cut -d= -f2- || true)
if [ -n "$eid" ]; then
    PROD_ENV="${PROD_ENV}CHAT_EVENT_ID=${eid}\n"
fi

if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would sync $(echo $PROD_VARS | wc -w | tr -d ' ') vars to VPS .env"
else
    printf "%b" "$PROD_ENV" | ssh "$VPS" "cat > $VPS_DIR/server/.env"
    echo "  Synced $(echo $PROD_VARS | wc -w | tr -d ' ') vars to VPS"
fi

# --- Step 1: Backup VPS data locally ---
echo ""
echo "[1/6] Downloading VPS data backup..."
run mkdir -p "$LOCAL_BACKUPS"
run rsync -az --info=progress2 \
    "$VPS:$VPS_DIR/server/data/" \
    "$LOCAL_BACKUPS/$TIMESTAMP/"
if [ "$DRY_RUN" = false ]; then
    echo "  Saved to $LOCAL_BACKUPS/$TIMESTAMP/"
    ls -lh "$LOCAL_BACKUPS/$TIMESTAMP/"
fi

# --- Step 2: Backup on VPS (timestamped, keeps previous) ---
echo ""
echo "[2/6] Creating VPS-side backup..."
run ssh "$VPS" "cd $VPS_DIR && cp -r server/data server/data.bak.$TIMESTAMP"
echo "  Created server/data.bak.$TIMESTAMP on VPS"

# --- Step 3: Pull latest code ---
echo ""
echo "[3/6] Pulling latest code on VPS..."
run ssh "$VPS" "cd $VPS_DIR && git pull origin main"

# --- Step 4: Rebuild and restart container ---
echo ""
echo "[4/6] Rebuilding container..."
run ssh "$VPS" "cd $VPS_DIR/server && docker compose up -d --build --force-recreate"

# --- Step 5: Health check ---
echo ""
echo "[5/6] Health check..."
if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would check container health + chat API"
else
    sleep 5
    STATUS=$(ssh "$VPS" "docker inspect stone-techno --format '{{.State.Health.Status}}' 2>/dev/null || echo 'unknown'")
    if [ "$STATUS" = "healthy" ]; then
        echo "  Container: healthy"
    else
        echo "  Container: $STATUS (waiting 30s...)"
        sleep 30
        STATUS=$(ssh "$VPS" "docker inspect stone-techno --format '{{.State.Health.Status}}' 2>/dev/null || echo 'unknown'")
        echo "  Container: $STATUS"
    fi

    CHAT_OK=$(ssh "$VPS" "docker exec stone-techno curl -sf http://localhost:8080/chat/api/config | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"ok\" if d.get(\"msg_char_limit\") else \"fail\")' 2>/dev/null || echo 'fail'")
    if [ "$CHAT_OK" = "ok" ]; then
        echo "  Chat API: responding"
    else
        echo "  WARNING: Chat API not responding!"
    fi
fi

# --- Cleanup old VPS backups (keep last 5) ---
run ssh "$VPS" "cd $VPS_DIR && ls -dt server/data.bak.* 2>/dev/null | tail -n +6 | xargs rm -rf"

echo ""
echo "=== Deploy complete ==="
echo "Backup: $LOCAL_BACKUPS/$TIMESTAMP/"
echo ""
echo "To deploy content (lineup HTML + photos):"
echo "  python stone_techno_companion.py --render-only --deploy"
