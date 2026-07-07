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
PROD_VARS="OPENAI_API_KEY MAILEROO_API_KEY VAPID_PRIVATE_KEY VAPID_PUBLIC_KEY VAPID_CLAIMS_EMAIL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET"
# Optional vars: synced when present, never block the deploy
OPTIONAL_VARS="CHAT_ADMIN_EMAILS CHAT_EMAIL_FROM CHAT_EVENT_ID"

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
    val=$(grep "^${var}=" "$LOCAL_ENV" | cut -d= -f2- || true)
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
# Add optional vars if present
for var in $OPTIONAL_VARS; do
    val=$(grep "^${var}=" "$LOCAL_ENV" 2>/dev/null | cut -d= -f2- || true)
    if [ -n "$val" ]; then
        PROD_ENV="${PROD_ENV}${var}=${val}\n"
    fi
done

NVARS=$(printf "%b" "$PROD_ENV" | grep -c '=' | tr -d ' ')
if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would sync $NVARS vars to VPS .env"
else
    # Atomic write: temp file + size check + mv, so a dropped connection
    # can never leave a truncated .env in place. Back up the existing .env
    # before overwriting (rollback point), and chmod 600 the result so
    # secrets aren't group/world-readable.
    ENV_BYTES=$(printf "%b" "$PROD_ENV" | wc -c | tr -d ' ')
    printf "%b" "$PROD_ENV" | ssh "$VPS" \
        "cat > $VPS_DIR/server/.env.tmp \
         && [ \$(wc -c < $VPS_DIR/server/.env.tmp) -eq $ENV_BYTES ] \
         && ([ ! -f $VPS_DIR/server/.env ] || cp $VPS_DIR/server/.env $VPS_DIR/server/.env.bak.$TIMESTAMP) \
         && mv $VPS_DIR/server/.env.tmp $VPS_DIR/server/.env \
         && chmod 600 $VPS_DIR/server/.env"
    echo "  Synced $NVARS vars to VPS"
fi

# --- Step 1: Backup VPS data locally ---
echo ""
echo "[1/6] Downloading VPS data backup..."
# Checkpoint WAL-mode SQLite DBs first so the copied .db files are
# self-contained and consistent (committed data may otherwise live only
# in -wal sidecars captured at a different instant)
if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would checkpoint SQLite WALs on VPS"
else
    ssh "$VPS" "python3 - <<'PY'
import glob, sqlite3
for db in glob.glob('$VPS_DIR/server/data/*.db'):
    try:
        c = sqlite3.connect(db, timeout=10)
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        c.close()
        print('  checkpointed ' + db)
    except Exception as e:
        print('  WARN: checkpoint failed for %s: %s' % (db, e))
PY"
fi
run mkdir -p "$LOCAL_BACKUPS"
run rsync -az --progress \
    "$VPS:$VPS_DIR/server/data/" \
    "$LOCAL_BACKUPS/$TIMESTAMP/"
if [ "$DRY_RUN" = false ]; then
    echo "  Saved to $LOCAL_BACKUPS/$TIMESTAMP/"
    ls -lh "$LOCAL_BACKUPS/$TIMESTAMP/"
    # Verify the downloaded backup is restorable before anything destructive
    for db in "$LOCAL_BACKUPS/$TIMESTAMP"/*.db; do
        [ -f "$db" ] || continue
        CHECK=$(python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA quick_check').fetchone()[0])" "$db" 2>&1 || echo "error")
        if [ "$CHECK" = "ok" ]; then
            echo "  Integrity ok: $(basename "$db")"
        else
            echo "  ERROR: backup integrity check failed for $(basename "$db"): $CHECK"
            echo "  Aborting before any change to the VPS."
            exit 1
        fi
    done
    # Best-effort: back up live user uploads too (24h TTL of media)
    rsync -az --progress "$VPS:$VPS_DIR/server/chat-uploads/" \
        "$LOCAL_BACKUPS/$TIMESTAMP/chat-uploads/" 2>/dev/null || \
        echo "  (no chat-uploads dir on VPS yet, skipping)"
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
    if [ "$STATUS" != "healthy" ]; then
        echo "  ERROR: container not healthy after deploy ($STATUS)."
        echo "  Previous data backup is at $LOCAL_BACKUPS/$TIMESTAMP/ and on the VPS."
        echo "  Investigate (docker logs stone-techno) or roll back before retrying."
        exit 1
    fi

    CHAT_OK=$(ssh "$VPS" "docker exec stone-techno curl -sf http://localhost:8080/chat/api/config | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"ok\" if d.get(\"msg_char_limit\") else \"fail\")' 2>/dev/null || echo 'fail'")
    if [ "$CHAT_OK" = "ok" ]; then
        echo "  Chat API: responding"
    else
        echo "  ERROR: Chat API not responding after deploy!"
        exit 1
    fi
fi

# --- Cleanup old VPS backups (keep last 5) ---
to_prune=$(ssh "$VPS" "ls -dt $VPS_DIR/server/data.bak.* 2>/dev/null | tail -n +6" || true)
if [ -n "$to_prune" ]; then
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY RUN] Would prune:"
        echo "$to_prune"
    else
        echo "$to_prune" | ssh "$VPS" "xargs rm -rf"
    fi
fi

echo ""
echo "=== Deploy complete ==="
echo "Backup: $LOCAL_BACKUPS/$TIMESTAMP/"
echo ""
echo "To deploy content (lineup HTML + photos):"
echo "  python pipeline/stone_techno_companion.py --render-only --deploy"
