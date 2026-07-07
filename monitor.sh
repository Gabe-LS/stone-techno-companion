#!/usr/bin/env bash
# Stone Techno Companion — health monitor
#
# Checks the live app from the outside (HTTP, TLS, latency) and the VPS from
# the inside (container health, disk, memory, CPU, DB integrity, log errors).
# Prints one OK/WARN/FAIL line per check. Exit code: 0 = all OK (warnings
# allowed with --strict off), 1 = at least one FAIL (or WARN with --strict).
#
# Run hourly from this Mac (needs ssh access to the VPS):
#   crontab -e
#   0 * * * * cd "/Users/gabrielelosurdo/Documents/Developer/Scripts/Personal/Stone Techno Companion" && ./monitor.sh --quiet >> logs/monitor.log 2>&1
#
# --quiet prints nothing when everything is OK (cron-friendly: the log only
# grows when something needs attention). --strict exits 1 on WARN too.
#
# Alerts: any FAIL (or WARN with --strict) fires a push notification via
# ntfy.sh to the private topic below, plus a macOS notification when run
# interactively. To receive pushes: install the ntfy app (iOS/Android) and
# subscribe to the topic. Test the pipeline with: ./monitor.sh --test-alert
# NOTE: the monitor only runs while this Mac is awake — pair it with an
# external uptime service for coverage when the Mac sleeps (see runbook).

set -uo pipefail
cd "$(dirname "$0")"

# Self-rotate the cron log: keep the newest ~256 KB once it passes 512 KB.
mkdir -p logs
MONITOR_LOG="logs/monitor.log"
if [ -f "$MONITOR_LOG" ] && [ "$(wc -c < "$MONITOR_LOG" | tr -d ' ')" -gt 524288 ]; then
    tail -c 262144 "$MONITOR_LOG" > "$MONITOR_LOG.tmp" && mv "$MONITOR_LOG.tmp" "$MONITOR_LOG"
fi

SITE="https://stonetechno.deftlab.dev"
HOST="stonetechno.deftlab.dev"
VPS="root@209.38.244.136"
VPS_DIR="/root/services/stone-techno"
NTFY_TOPIC="stc26-ops-2c8faa31e3be"   # private: knowing the name = receiving the alerts

QUIET=false
STRICT=false
for arg in "$@"; do
    case "$arg" in
        --quiet) QUIET=true ;;
        --strict) STRICT=true ;;
        --test-alert)
            curl -s -m 10 -H "Title: Stone Techno monitor test" -H "Priority: high" \
                -H "Tags: white_check_mark" -d "Test alert — the pipeline works." \
                "https://ntfy.sh/$NTFY_TOPIC" >/dev/null \
                && echo "Test alert sent to ntfy.sh/$NTFY_TOPIC" \
                || echo "ERROR: could not reach ntfy.sh"
            command -v osascript >/dev/null && osascript -e \
                'display notification "Test alert — the pipeline works." with title "Stone Techno monitor"' || true
            exit 0
            ;;
    esac
done

send_alert() {
    # $1 = summary line, $2 = body
    curl -s -m 10 -H "Title: $1" -H "Priority: high" -H "Tags: rotating_light" \
        -d "$2" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null || true
    command -v osascript >/dev/null && osascript -e \
        "display notification \"$1\" with title \"Stone Techno monitor\"" 2>/dev/null || true
}

PASS=0; WARN=0; FAIL=0
LINES=""
report() { LINES="${LINES}${1}\n"; }
ok()   { PASS=$((PASS+1)); report "  OK    $1"; }
warn() { WARN=$((WARN+1)); report "  WARN  $1"; }
fail() { FAIL=$((FAIL+1)); report "  FAIL  $1"; }

# --- External checks -------------------------------------------------------

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$SITE/line-up" || echo "000")
[ "$code" = "200" ] && ok "lineup page (200)" || fail "lineup page (HTTP $code)"

# Chat API must return real JSON — the old catch-all served HTML with a 200,
# so a status check alone is not enough.
cfg_ok=$(curl -s --max-time 15 "$SITE/chat/api/config" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print('yes' if d.get('msg_char_limit') else 'no')
except Exception:
    print('no')" 2>/dev/null || echo "no")
[ "$cfg_ok" = "yes" ] && ok "chat API config (valid JSON)" || fail "chat API config (not valid JSON — chat down or not deployed)"

for path in /shared.css /sw.js /manifest.json /privacy; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$SITE$path" || echo "000")
    [ "$code" = "200" ] && ok "static $path (200)" || fail "static $path (HTTP $code)"
done

# Chat WebSocket route must be reachable THROUGH the proxy. An unauthenticated
# probe gets 101 (accepted then closed) or 400/403 from the app — all prove the
# upgrade path works. 404/5xx means Caddy or the app is not routing WS.
ws_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
    -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    "$SITE/ws/chat/monitor-probe" || echo "000")
case "$ws_code" in
    101|400|403) ok "chat WebSocket upgrade path ($ws_code)" ;;
    *)           fail "chat WebSocket upgrade path (HTTP $ws_code)" ;;
esac

# Lineup data endpoints: bios lazy-load + favorites API (hearts.db path)
bios_ok=$(curl -s --max-time 15 "$SITE/bios.json" | python3 -c "import json,sys; json.load(sys.stdin); print('yes')" 2>/dev/null || echo no)
[ "$bios_ok" = "yes" ] && ok "bios.json (valid JSON)" || fail "bios.json (not valid JSON)"
me_ok=$(curl -s --max-time 15 "$SITE/api/me" | python3 -c "import json,sys; json.load(sys.stdin); print('yes')" 2>/dev/null || echo no)
[ "$me_ok" = "yes" ] && ok "favorites API /api/me (valid JSON)" || fail "favorites API /api/me (not valid JSON)"

# Meetup map POIs (MapTiler dataset -> server-side fetch -> JSON list)
pois_ok=$(curl -s --max-time 15 "$SITE/chat/api/pois" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('yes' if isinstance(d, list) and len(d) > 0 else 'empty')" 2>/dev/null || echo no)
case "$pois_ok" in
    yes)   ok "meetup POIs (non-empty list)" ;;
    empty) warn "meetup POIs (empty list — MapTiler down with cold cache?)" ;;
    *)     fail "meetup POIs (not valid JSON)" ;;
esac

# TLS expiry — Caddy renews ~30 days out, so <21 days means renewal is failing
expiry=$(echo | openssl s_client -servername "$HOST" -connect "$HOST:443" 2>/dev/null \
    | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
if [ -n "$expiry" ]; then
    exp_epoch=$(date -j -f "%b %e %T %Y %Z" "$expiry" +%s 2>/dev/null || date -d "$expiry" +%s 2>/dev/null)
    days=$(( (exp_epoch - $(date +%s)) / 86400 ))
    if [ "$days" -lt 7 ]; then fail "TLS cert expires in ${days}d"
    elif [ "$days" -lt 21 ]; then warn "TLS cert expires in ${days}d (Caddy renewal may be failing)"
    else ok "TLS cert (${days}d left)"; fi
else
    fail "TLS cert (could not read expiry)"
fi

t=$(curl -s -o /dev/null -w '%{time_total}' --max-time 15 "$SITE/line-up" || echo "99")
slow=$(python3 -c "print('fail' if $t > 8 else 'warn' if $t > 3 else 'ok')")
case "$slow" in
    ok)   ok "lineup latency (${t}s)" ;;
    warn) warn "lineup latency (${t}s)" ;;
    *)    fail "lineup latency (${t}s)" ;;
esac

# --- VPS checks (single ssh round-trip) -------------------------------------

VPS_REPORT=$(ssh -o ConnectTimeout=15 "$VPS" bash -s <<EOF 2>/dev/null
health=\$(docker inspect stone-techno --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)
restarts=\$(docker inspect stone-techno --format '{{.RestartCount}}' 2>/dev/null || echo -1)
disk=\$(df -P / | awk 'NR==2 {gsub("%",""); print \$5}')
mem_avail=\$(free | awk '/Mem:/ {printf "%d", \$7*100/\$2}')
load1=\$(cut -d' ' -f1 /proc/loadavg)
cores=\$(nproc)
uploads_mb=\$(du -sm $VPS_DIR/server/chat-uploads 2>/dev/null | cut -f1)
uploads_mb=\${uploads_mb:-0}
errors_1h=\$(docker logs --since 1h stone-techno 2>&1 | grep -cE 'ERROR|CRITICAL|Traceback' || true)
vapid_ok=\$(docker logs stone-techno 2>&1 | grep -c 'VAPID key pair verified' || true)
mod=\$(docker exec stone-techno python3 - <<'PYMOD' 2>/dev/null
import os
k = os.environ.get('OPENAI_API_KEY')
if not k:
    print('nokey')
else:
    import httpx
    try:
        r = httpx.post('https://api.openai.com/v1/moderations',
                       json={'model': 'omni-moderation-latest', 'input': 'ping'},
                       headers={'Authorization': 'Bearer ' + k}, timeout=10)
        print('ok' if r.status_code == 200 else 'http%d' % r.status_code)
    except Exception:
        print('err')
PYMOD
)
mod=\${mod:-execfail}
dbcheck=\$(python3 - <<'PY'
import glob, sqlite3
bad = []
for db in glob.glob('$VPS_DIR/server/data/*.db'):
    try:
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10)
        r = c.execute('PRAGMA quick_check').fetchone()[0]
        c.close()
        if r != 'ok':
            bad.append(db.split('/')[-1])
    except Exception:
        bad.append(db.split('/')[-1])
print(','.join(bad) if bad else 'ok')
PY
)
echo "health=\$health restarts=\$restarts disk=\$disk mem_avail=\$mem_avail load1=\$load1 cores=\$cores uploads_mb=\$uploads_mb errors_1h=\$errors_1h dbcheck=\$dbcheck vapid_ok=\$vapid_ok mod=\$mod"
EOF
)

if [ -z "$VPS_REPORT" ]; then
    fail "VPS unreachable over ssh"
else
    eval "$(echo "$VPS_REPORT" | tr ' ' '\n')"

    [ "$health" = "healthy" ] && ok "container health (healthy)" || fail "container health ($health)"
    if [ "$restarts" -gt 0 ]; then warn "container restarts since start: $restarts"; else ok "container restarts (0)"; fi

    if [ "$disk" -ge 93 ]; then fail "disk usage ${disk}%"
    elif [ "$disk" -ge 85 ]; then warn "disk usage ${disk}%"
    else ok "disk usage (${disk}%)"; fi

    if [ "$mem_avail" -le 7 ]; then fail "memory available ${mem_avail}%"
    elif [ "$mem_avail" -le 15 ]; then warn "memory available ${mem_avail}%"
    else ok "memory available (${mem_avail}%)"; fi

    high=$(python3 -c "print('fail' if $load1 > 2*$cores else 'warn' if $load1 > $cores else 'ok')")
    case "$high" in
        ok)   ok "load average (${load1} on ${cores} cores)" ;;
        warn) warn "load average (${load1} on ${cores} cores)" ;;
        *)    fail "load average (${load1} on ${cores} cores)" ;;
    esac

    [ "$dbcheck" = "ok" ] && ok "DB integrity (quick_check)" || fail "DB integrity: $dbcheck"

    # Startup log line proves the push signing key pair is consistent. WARN not
    # FAIL when absent: the line can rotate out of capped logs under traffic.
    if [ "$vapid_ok" -ge 1 ] 2>/dev/null; then ok "VAPID key pair verified (startup log)"
    else warn "VAPID verified line not in container logs (rotated out, or key check failed — verify manually)"; fi

    # Moderation FAILS CLOSED: if OpenAI is unreachable, every message in
    # moderated rooms is rejected while everything else looks green.
    case "$mod" in
        ok)       ok "moderation API reachable from container" ;;
        nokey)    fail "OPENAI_API_KEY not set in container — moderated rooms are word-filter only" ;;
        execfail) fail "moderation check could not run in container (old image or python/httpx missing)" ;;
        *)        fail "moderation API unreachable from container ($mod) — sends in moderated rooms are being rejected" ;;
    esac

    if [ "$errors_1h" -gt 20 ]; then warn "container log errors last hour: $errors_1h"
    elif [ "$errors_1h" -gt 0 ]; then warn "container log errors last hour: $errors_1h (docker logs --since 1h stone-techno)"
    else ok "container log errors last hour (0)"; fi

    ok "uploads dir ${uploads_mb} MB (info)"
fi

# --- Summary -----------------------------------------------------------------

STATUS="OK"
[ "$WARN" -gt 0 ] && STATUS="WARN"
[ "$FAIL" -gt 0 ] && STATUS="FAIL"

if [ "$QUIET" = false ] || [ "$STATUS" != "OK" ]; then
    echo "=== Stone Techno monitor — $(date '+%Y-%m-%d %H:%M:%S') — $STATUS ($PASS ok, $WARN warn, $FAIL fail) ==="
    printf "%b" "$LINES"
fi

ALERT=false
[ "$FAIL" -gt 0 ] && ALERT=true
[ "$STRICT" = true ] && [ "$WARN" -gt 0 ] && ALERT=true
if [ "$ALERT" = true ]; then
    PROBLEMS=$(printf "%b" "$LINES" | grep -E "FAIL|WARN" | sed 's/^ *//')
    send_alert "Stone Techno: $STATUS ($FAIL fail, $WARN warn)" "$PROBLEMS"
fi

[ "$FAIL" -gt 0 ] && exit 1
[ "$STRICT" = true ] && [ "$WARN" -gt 0 ] && exit 1
exit 0
