#!/bin/sh
# Fetch the latest monitor.sh from GitHub, fall back to the cached copy.
# Called by cron; exec's monitor.sh so the cron process becomes monitor.sh.
RAW_URL="https://raw.githubusercontent.com/Gabe-LS/stone-techno-companion/main/monitor.sh"

AUTH_HEADER=""
if [ -n "${GITHUB_TOKEN:-}" ]; then
    AUTH_HEADER="Authorization: token $GITHUB_TOKEN"
fi

if curl -fsSL --max-time 10 ${AUTH_HEADER:+-H "$AUTH_HEADER"} \
        "$RAW_URL" -o /app/monitor.sh.new 2>/dev/null \
   && [ -s /app/monitor.sh.new ]; then
    chmod +x /app/monitor.sh.new
    mv /app/monitor.sh.new /app/monitor.sh
else
    rm -f /app/monitor.sh.new
fi

exec /app/monitor.sh "$@"
