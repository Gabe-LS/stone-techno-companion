#!/usr/bin/env bash
set -euo pipefail

# Stone Techno Companion — deploy script
# Deploys server code to VPS with timestamped backup + local copy

VPS="root@209.38.244.136"
VPS_DIR="/root/services/stone-techno"
LOCAL_BACKUPS="backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=== Stone Techno Deploy ==="
echo ""

# --- Step 1: Backup VPS data locally ---
echo "[1/5] Downloading VPS data backup..."
mkdir -p "$LOCAL_BACKUPS"
rsync -az --info=progress2 \
    "$VPS:$VPS_DIR/server/data/" \
    "$LOCAL_BACKUPS/$TIMESTAMP/"
echo "  Saved to $LOCAL_BACKUPS/$TIMESTAMP/"
ls -lh "$LOCAL_BACKUPS/$TIMESTAMP/"

# --- Step 2: Backup on VPS (timestamped, keeps previous) ---
echo ""
echo "[2/5] Creating VPS-side backup..."
ssh "$VPS" "cd $VPS_DIR && cp -r server/data server/data.bak.$TIMESTAMP"
echo "  Created server/data.bak.$TIMESTAMP on VPS"

# --- Step 3: Pull latest code ---
echo ""
echo "[3/5] Pulling latest code on VPS..."
ssh "$VPS" "cd $VPS_DIR && git pull origin main"

# --- Step 4: Rebuild and restart container ---
echo ""
echo "[4/5] Rebuilding container..."
ssh "$VPS" "cd $VPS_DIR/server && docker compose up -d --build --force-recreate"

# --- Step 5: Health check ---
echo ""
echo "[5/5] Health check..."
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

# --- Cleanup old VPS backups (keep last 5) ---
ssh "$VPS" "cd $VPS_DIR && ls -dt server/data.bak.* 2>/dev/null | tail -n +6 | xargs rm -rf"

echo ""
echo "=== Deploy complete ==="
echo "Backup: $LOCAL_BACKUPS/$TIMESTAMP/"
echo ""
echo "To deploy content (lineup HTML + photos):"
echo "  python stone_techno_companion.py --render-only --deploy"
