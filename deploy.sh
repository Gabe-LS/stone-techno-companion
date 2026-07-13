#!/usr/bin/env bash
set -euo pipefail

# Stone Techno Companion — deploy script
# Deploys server code to VPS with timestamped backup + local copy
# Usage: ./deploy.sh [--dry-run] [--ref <branch|tag|commit>]
#        # --ref defaults to main; use it to ship a branch (e.g. for public
#        # testing) or a tag without touching main. Reverting is just
#        # ./deploy.sh --ref main (or --rollback <tag>).
#        ./deploy.sh --rollback <commit|tag>   # reset VPS code to target + rebuild
#                                          # (code only — data/.env restore stays
#                                          # manual, see docs/runbook.md)

cd "$(dirname "$0")"

VPS="root@209.38.244.136"
VPS_DIR="/root/services/stone-techno"
LOCAL_BACKUPS="backups"
LOCAL_ENV="services/companion/.env"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DRY_RUN=false
DEPLOY_REF="main"

check_container_health() {
    # Sets STATUS. Waits up to ~35s for the healthcheck to settle.
    sleep 5
    STATUS=$(ssh "$VPS" "docker inspect stone-techno --format '{{.State.Health.Status}}' 2>/dev/null || echo 'unknown'")
    if [ "$STATUS" != "healthy" ]; then
        echo "  Container: $STATUS (waiting 30s...)"
        sleep 30
        STATUS=$(ssh "$VPS" "docker inspect stone-techno --format '{{.State.Health.Status}}' 2>/dev/null || echo 'unknown'")
    fi
    echo "  Container: $STATUS"
}

if [ "${1:-}" = "--rollback" ]; then
    TARGET="${2:-}"
    if [ -z "$TARGET" ]; then
        echo "Usage: ./deploy.sh --rollback <commit>"
        exit 1
    fi
    echo "=== Stone Techno Rollback -> $TARGET ==="
    echo ""
    echo "[1/3] Fetching refs and verifying target on VPS..."
    # Fetch first so a tag/branch pushed to origin just now (e.g. a known-good
    # tag created locally) resolves on the VPS instead of failing "not found".
    if ! ssh "$VPS" "cd $VPS_DIR && git fetch origin --tags --prune --force >/dev/null 2>&1 && git rev-parse --verify --quiet $TARGET^{commit}" >/dev/null; then
        echo "  ERROR: target '$TARGET' not found in the VPS repo (after fetch)"
        exit 1
    fi
    echo "[2/3] Resetting VPS worktree and rebuilding container..."
    ssh "$VPS" "cd $VPS_DIR && git reset --hard $TARGET"
    ssh "$VPS" "cd $VPS_DIR/services/companion && docker compose up -d --build --force-recreate"
    echo "[3/3] Health check (container only — target commit may predate the chat API)..."
    check_container_health
    if [ "$STATUS" != "healthy" ]; then
        echo "  ERROR: container not healthy after rollback ($STATUS)."
        echo "  Check: ssh $VPS 'docker logs stone-techno --tail 100'"
        exit 1
    fi
    echo ""
    echo "=== Rollback complete (code only) ==="
    echo "If data or .env were damaged, restore manually on the VPS:"
    echo "  cp -r $VPS_DIR/services/companion/data.bak.<timestamp>/. $VPS_DIR/services/companion/data/"
    echo "  cp $VPS_DIR/services/companion/.env.bak.<timestamp> $VPS_DIR/services/companion/.env"
    echo "(then docker compose restart; see docs/runbook.md)"
    exit 0
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        --ref)
            DEPLOY_REF="${2:-}"
            if [ -z "$DEPLOY_REF" ]; then
                echo "Usage: ./deploy.sh [--dry-run] [--ref <branch|tag|commit>]"
                exit 1
            fi
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: ./deploy.sh [--dry-run] [--ref <branch|tag|commit>]"
            exit 1
            ;;
    esac
    shift
done

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
OPTIONAL_VARS="CHAT_ADMIN_EMAILS CHAT_EMAIL_FROM CHAT_EVENT_ID MAPTILER_KEY MAPTILER_DATASET_ID"

if [ "$DRY_RUN" = true ]; then
    echo "=== Stone Techno Deploy (DRY RUN) ==="
else
    echo "=== Stone Techno Deploy ==="
fi
echo "  Ref: $DEPLOY_REF"
echo ""

# --- Step 0: Sync env vars to VPS ---
echo "[0/7] Syncing env vars to VPS..."
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
        "cat > $VPS_DIR/services/companion/.env.tmp \
         && [ \$(wc -c < $VPS_DIR/services/companion/.env.tmp) -eq $ENV_BYTES ] \
         && ([ ! -f $VPS_DIR/services/companion/.env ] || cp $VPS_DIR/services/companion/.env $VPS_DIR/services/companion/.env.bak.$TIMESTAMP) \
         && mv $VPS_DIR/services/companion/.env.tmp $VPS_DIR/services/companion/.env \
         && chmod 600 $VPS_DIR/services/companion/.env"
    echo "  Synced $NVARS vars to VPS"
fi

# --- Step 1: VAPID key preflight ---
# The .env sync above pushes the LOCAL VAPID_PUBLIC_KEY to production, but the
# private key production signs with is the pem already on the VPS. If they do
# not form a pair, every push fails silently (FCM rejects, Apple/Mozilla may
# not). Verify BEFORE anything changes.
echo ""
echo "[1/7] VAPID preflight (local public key vs VPS private pem)..."
LOCAL_VAPID_PUB=$(grep "^VAPID_PUBLIC_KEY=" "$LOCAL_ENV" | cut -d= -f2-)
VPS_PEM_EXISTS=$(ssh "$VPS" "[ -f $VPS_DIR/services/companion/data/vapid_private.pem ] && echo yes || echo no")
if [ "$VPS_PEM_EXISTS" = "no" ]; then
    echo "  No vapid_private.pem on VPS yet — skipping (first deploy of push?)"
else
    VPS_VAPID_PUB=$(ssh "$VPS" "cat $VPS_DIR/services/companion/data/vapid_private.pem" | python3 -c "
import sys, base64
from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PublicFormat
key = load_pem_private_key(sys.stdin.buffer.read(), password=None)
pub = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
print(base64.urlsafe_b64encode(pub).rstrip(b'=').decode())")
    if [ "$LOCAL_VAPID_PUB" = "$VPS_VAPID_PUB" ]; then
        echo "  VAPID key pair verified (local public key matches VPS pem)"
    else
        echo "  ERROR: local VAPID_PUBLIC_KEY does not match the VPS vapid_private.pem."
        echo "  Deploying would break every push subscription. Fix ONE of:"
        echo "    a) put the VPS-matching public key in $LOCAL_ENV:"
        echo "       VAPID_PUBLIC_KEY=$VPS_VAPID_PUB"
        echo "    b) or replace the VPS pem with the local one (invalidates"
        echo "       existing prod subscriptions):"
        echo "       scp <local-pem> $VPS:$VPS_DIR/services/companion/data/vapid_private.pem"
        exit 1
    fi
fi

# --- Step 2: Backup VPS data locally ---
echo ""
echo "[2/7] Downloading VPS data backup..."
# VACUUM INTO writes a transactionally consistent snapshot of each DB even
# under concurrent writes (no torn .db/-wal pairs, no checkpoint needed).
# The snapshot dir then becomes both the local download AND the VPS-side
# backup (step 3), so both backups are the same verified bytes.
if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would snapshot DBs on VPS via VACUUM INTO"
else
    ssh "$VPS" "python3 - <<'PY'
import glob, os, shutil, sqlite3
snap = '$VPS_DIR/services/companion/data-snap.$TIMESTAMP'
os.makedirs(snap, exist_ok=True)
for db in glob.glob('$VPS_DIR/services/companion/data/*.db'):
    dest = os.path.join(snap, os.path.basename(db))
    c = sqlite3.connect(db, timeout=10)
    c.execute('VACUUM INTO ?', (dest,))
    c.close()
    print('  snapshot ' + os.path.basename(db))
for f in glob.glob('$VPS_DIR/services/companion/data/*.pem'):
    shutil.copy2(f, snap)
    print('  copied ' + os.path.basename(f))
PY"
fi
run mkdir -p "$LOCAL_BACKUPS"
run rsync -az --progress \
    "$VPS:$VPS_DIR/services/companion/data-snap.$TIMESTAMP/" \
    "$LOCAL_BACKUPS/$TIMESTAMP/"
if [ "$DRY_RUN" = false ]; then
    echo "  Saved to $LOCAL_BACKUPS/$TIMESTAMP/"
    ls -lh "$LOCAL_BACKUPS/$TIMESTAMP/"
    # An empty backup must fail loudly — a backup gate that checks nothing
    # is worse than none (it reads as "covered" when it isn't).
    DB_COUNT=$(find "$LOCAL_BACKUPS/$TIMESTAMP" -maxdepth 1 -name "*.db" | wc -l | tr -d ' ')
    if [ "$DB_COUNT" -eq 0 ]; then
        echo "  ERROR: backup contains no .db files — aborting before any change."
        exit 1
    fi
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
    rsync -az --progress "$VPS:$VPS_DIR/services/companion/chat-uploads/" \
        "$LOCAL_BACKUPS/$TIMESTAMP/chat-uploads/" 2>/dev/null || \
        echo "  (no chat-uploads dir on VPS yet, skipping)"
fi

# --- Step 3: Backup on VPS (timestamped, keeps previous) ---
echo ""
echo "[3/7] Creating VPS-side backup..."
run ssh "$VPS" "mv $VPS_DIR/services/companion/data-snap.$TIMESTAMP $VPS_DIR/services/companion/data.bak.$TIMESTAMP"
echo "  Created services/companion/data.bak.$TIMESTAMP on VPS (verified snapshot)"

# --- Step 4: Check out target ref on VPS ---
# Fetch everything, then resolve DEPLOY_REF: a remote branch ships its latest
# tip (origin/<ref>), a tag or commit ships that exact object. Always lands in
# a detached HEAD at the precise commit -- no local branch state to drift on a
# deploy box, and the existing `git reset --hard` rollback works from it.
echo ""
echo "[4/7] Fetching and checking out '$DEPLOY_REF' on VPS..."
run ssh "$VPS" "cd $VPS_DIR && git fetch origin --tags --prune --force && \
  if git rev-parse -q --verify \"origin/$DEPLOY_REF^{commit}\" >/dev/null; then TGT=\"origin/$DEPLOY_REF\"; \
  elif git rev-parse -q --verify \"$DEPLOY_REF^{commit}\" >/dev/null; then TGT=\"$DEPLOY_REF\"; \
  else echo \"  ERROR: ref '$DEPLOY_REF' not found on origin\" >&2; exit 1; fi && \
  git checkout --force --detach \"\$TGT\" && echo \"  Checked out \$(git rev-parse --short HEAD) ($DEPLOY_REF)\""

# --- Step 5: Seed chat.db (first chat deploy only) ---
# Builds a clean production chat.db from the LOCAL dev database: keeps the
# curated group rooms + chat_settings, strips all messages/users/test data,
# and pre-creates the owner account (see services/companion/seed_chat_db.py). Uploaded
# ONLY when the VPS has no chat.db — this can never overwrite live prod data.
echo ""
echo "[5/7] Chat DB seed..."
VPS_HAS_CHATDB=$(ssh "$VPS" "[ -f $VPS_DIR/services/companion/data/chat.db ] && echo yes || echo no")
if [ "$VPS_HAS_CHATDB" = "yes" ]; then
    echo "  chat.db already exists on VPS — skipping seed (live data untouched)"
elif [ ! -f "services/companion/data/chat.db" ]; then
    echo "  No local chat.db to seed from — container will create a fresh one"
else
    SEED_EMAIL="gabrielelosurdo@gmail.com"
    if ! grep "^CHAT_ADMIN_EMAILS=" "$LOCAL_ENV" | grep -q "$SEED_EMAIL"; then
        echo "  WARNING: $SEED_EMAIL is not in CHAT_ADMIN_EMAILS in $LOCAL_ENV —"
        echo "  the seeded owner account will NOT be super-admin. Ctrl-C to fix, or continuing in 10s..."
        [ "$DRY_RUN" = true ] || sleep 10
    fi
    SEED_TMP=$(mktemp -d)
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY RUN] Would build seed from services/companion/data/chat.db and upload"
    else
        python3 services/companion/seed_chat_db.py --out "$SEED_TMP/chat.db" --email "$SEED_EMAIL"
        rsync -az "$SEED_TMP/chat.db" "$VPS:$VPS_DIR/services/companion/data/chat.db"
        echo "  Seeded chat.db uploaded to VPS"
    fi
    rm -rf "$SEED_TMP"
fi

# --- Step 6: Rebuild and restart container ---
echo ""
echo "[6/7] Rebuilding container..."
# --force-recreate destroys the old container and its logs — archive them
# first (best-effort: the container may not exist on a fresh server).
if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would archive previous container log to logs/docker-$TIMESTAMP.log"
else
    mkdir -p logs
    ssh "$VPS" "docker logs stone-techno 2>&1" > "logs/docker-$TIMESTAMP.log" 2>/dev/null \
        && echo "  Archived previous container log ($(wc -l < "logs/docker-$TIMESTAMP.log" | tr -d ' ') lines)" \
        || echo "  (no previous container log to archive)"
    # Keep the newest 15 archived container logs
    ls -dt logs/docker-*.log 2>/dev/null | tail -n +16 | xargs rm -f 2>/dev/null || true
fi
run ssh "$VPS" "cd $VPS_DIR/services/companion && docker compose up -d --build --force-recreate"

# --- Step 7: Health check ---
echo ""
echo "[7/7] Health check..."
if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would check container health + chat API"
else
    check_container_health
    if [ "$STATUS" != "healthy" ]; then
        echo "  ERROR: container not healthy after deploy ($STATUS)."
        echo "  Previous data backup is at $LOCAL_BACKUPS/$TIMESTAMP/ and on the VPS."
        echo "  Investigate (docker logs stone-techno) or roll back:"
        echo "    ./deploy.sh --rollback <previous-commit>"
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

# --- Cleanup old backups ---
# VPS: keep last 5 data backups + last 5 .env backups.
to_prune=$(ssh "$VPS" "ls -dt $VPS_DIR/services/companion/data.bak.* 2>/dev/null | tail -n +6; ls -dt $VPS_DIR/services/companion/.env.bak.* 2>/dev/null | tail -n +6" || true)
if [ -n "$to_prune" ]; then
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY RUN] Would prune on VPS:"
        echo "$to_prune"
    else
        echo "$to_prune" | ssh "$VPS" "xargs rm -rf"
    fi
fi
# Local: keep the newest 15 backup dirs (each can be large during the festival
# — chat.db + docker.log + a full chat-uploads copy).
local_prune=$(ls -dt "$LOCAL_BACKUPS"/2*/ 2>/dev/null | tail -n +16 || true)
if [ -n "$local_prune" ]; then
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY RUN] Would prune locally:"
        echo "$local_prune"
    else
        echo "$local_prune" | xargs rm -rf
        echo "  Pruned old local backups (kept newest 15)"
    fi
fi

echo ""
echo "=== Deploy complete ==="
echo "Backup: $LOCAL_BACKUPS/$TIMESTAMP/"
echo ""
echo "To deploy content (lineup HTML + photos):"
echo "  python services/data/stone_techno_companion.py --render-only --deploy"
