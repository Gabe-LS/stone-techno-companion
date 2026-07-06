#!/bin/sh
set -e

# Fix ownership of bind-mounted volumes that may be root-owned from
# the previous root-user container. Runs as root, then drops to appuser.
chown -R appuser:appuser /app/data /app/chat/uploads 2>/dev/null || true

# dop-cache may not exist yet on a fresh deploy
mkdir -p /app/static/vendor/dop-cache
chown -R appuser:appuser /app/static/vendor/dop-cache 2>/dev/null || true

exec gosu appuser "$@"
