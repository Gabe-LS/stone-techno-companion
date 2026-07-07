#!/bin/sh
set -e

# Fix ownership of bind-mounted volumes that may be root-owned from
# the previous root-user container. Runs as root, then drops to appuser.
# /app/static is included because api.py creates photos/, thumbs/, and
# vendor/ under it at startup, and the meetup map writes vendor/dop-cache.
mkdir -p /app/static/vendor/dop-cache
chown -R appuser:appuser /app/data /app/chat/uploads /app/static 2>/dev/null || true

exec gosu appuser "$@"
