#!/bin/sh
# Container entrypoint for the Stone Techno health monitor on QNAP.
#
# Copies the SSH key from the read-only bind mount into the container with
# strict permissions (QNAP share permissions are often too open for ssh,
# which refuses group/world-readable private keys), installs the cron
# schedule, runs one immediate check so a broken setup surfaces in
# `docker logs` right away, then hands over to crond in the foreground.
set -eu

mkdir -p /root/.ssh /app/logs
chmod 700 /root/.ssh

if [ -d /keys ]; then
    cp /keys/* /root/.ssh/ 2>/dev/null || true
    chmod 600 /root/.ssh/* 2>/dev/null || true
fi

if [ ! -f /root/.ssh/id_ed25519 ] && [ ! -f /root/.ssh/id_rsa ]; then
    echo "WARNING: no SSH key found in /keys - the VPS internal checks will fail."
    echo "Generate one with: ssh-keygen -t ed25519 -f ssh/id_ed25519 -N ''"
fi

# monitor.sh calls `ssh root@209.38.244.136` with no options, so pin the
# key and first-connection host key policy here. A user-provided config in
# the mounted key dir wins.
if [ ! -f /root/.ssh/config ]; then
    cat > /root/.ssh/config <<'EOF'
Host 209.38.244.136
    User root
    IdentityFile /root/.ssh/id_ed25519
    StrictHostKeyChecking accept-new
EOF
    chmod 600 /root/.ssh/config
fi

CRON_SCHEDULE="${CRON_SCHEDULE:-0 * * * *}"
echo "$CRON_SCHEDULE /app/monitor.sh --quiet >> /app/logs/monitor.log 2>&1" | crontab -
echo "Installed cron schedule: $CRON_SCHEDULE"

echo "Running one immediate check..."
/app/monitor.sh || true

exec crond -f -l 8
