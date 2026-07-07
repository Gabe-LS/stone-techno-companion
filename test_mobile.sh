#!/usr/bin/env bash
set -euo pipefail

# Mobile testing: starts the server on LAN + mitmproxy for iPhone
# Usage: ./test_mobile.sh [--stop]

cd "$(dirname "$0")"

LAN_IP=$(ifconfig en0 | grep 'inet ' | awk '{print $2}')
PORT=64728
PROXY_PORT=8082
ENV_FILE="server/.env"

if [ "${1:-}" = "--stop" ]; then
    echo "Stopping..."
    kill $(lsof -ti :$PORT 2>/dev/null) 2>/dev/null && echo "  Server stopped" || echo "  Server not running"
    kill $(lsof -ti :$PROXY_PORT 2>/dev/null) 2>/dev/null && echo "  Proxy stopped" || echo "  Proxy not running"
    # Restore CHAT_BASE_URL
    if grep -q "CHAT_BASE_URL=https://$LAN_IP" "$ENV_FILE" 2>/dev/null; then
        sed -i '' "s|CHAT_BASE_URL=https://$LAN_IP:$PORT|CHAT_BASE_URL=https://localhost:$PORT|" "$ENV_FILE"
        echo "  Restored CHAT_BASE_URL to localhost"
    fi
    exit 0
fi

echo "=== Mobile Testing Setup ==="
echo ""
echo "  LAN IP:  $LAN_IP"
echo "  Server:  https://$LAN_IP:$PORT"
echo "  Proxy:   $LAN_IP:$PROXY_PORT"
echo ""

# --- Step 1: Generate cert covering LAN IP ---
echo "[1/4] Checking TLS cert..."
CERT_COVERS=$(openssl x509 -in server/certs/localhost+1.pem -noout -text 2>/dev/null | grep -c "$LAN_IP" || true)
if [ "$CERT_COVERS" -eq 0 ]; then
    echo "  Regenerating cert for localhost + $LAN_IP..."
    mkdir -p server/certs
    cd server
    mkcert -cert-file certs/localhost+1.pem -key-file certs/localhost+1-key.pem localhost 127.0.0.1 "$LAN_IP"
    cd ..
else
    echo "  Cert already covers $LAN_IP"
fi

# --- Step 2: Update CHAT_BASE_URL for magic links ---
echo "[2/4] Setting CHAT_BASE_URL for LAN..."
if grep -q "CHAT_BASE_URL=https://localhost" "$ENV_FILE"; then
    sed -i '' "s|CHAT_BASE_URL=https://localhost:$PORT|CHAT_BASE_URL=https://$LAN_IP:$PORT|" "$ENV_FILE"
    echo "  Updated to https://$LAN_IP:$PORT"
elif grep -q "CHAT_BASE_URL=https://$LAN_IP" "$ENV_FILE"; then
    echo "  Already set to LAN IP"
else
    echo "  WARNING: CHAT_BASE_URL not found in .env"
fi

# --- Step 3: Start server on all interfaces ---
echo "[3/4] Starting server on 0.0.0.0:$PORT..."
kill $(lsof -ti :$PORT 2>/dev/null) 2>/dev/null || true
sleep 1
cd server && set -a && source .env && set +a
nohup uvicorn api:app --host 0.0.0.0 --port $PORT \
    --ssl-keyfile certs/localhost+1-key.pem --ssl-certfile certs/localhost+1.pem \
    > /dev/null 2>&1 &
cd ..
sleep 2
if lsof -i :$PORT | grep -q LISTEN; then
    echo "  Server running"
else
    echo "  ERROR: Server failed to start"
    exit 1
fi

# --- Step 4: Start mitmproxy ---
echo "[4/4] Starting mitmproxy on :$PROXY_PORT..."
kill $(lsof -ti :$PROXY_PORT 2>/dev/null) 2>/dev/null || true
sleep 1
nohup mitmdump -p $PROXY_PORT --set block_global=false --ssl-insecure --set connection_strategy=lazy -q \
    > /dev/null 2>&1 &
sleep 2
if lsof -i :$PROXY_PORT | grep -q LISTEN; then
    echo "  Proxy running"
else
    echo "  WARNING: Proxy failed to start (push still works without it)"
fi

echo ""
echo "=== Ready ==="
echo ""
echo "  On iPhone:"
echo "    1. Wi-Fi proxy: $LAN_IP:$PROXY_PORT"
echo "    2. Open: https://$LAN_IP:$PORT/chat"
echo "    3. Log in (Google OAuth works, email magic link points to LAN IP)"
echo ""
echo "  To test push:"
echo "    1. Enable notifications in chat settings"
echo "    2. Lock phone or switch app"
echo "    3. Send a message from Mac browser"
echo "    4. Notification should arrive on phone"
echo ""
echo "  To stop: ./test_mobile.sh --stop"
