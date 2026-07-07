FROM alpine:3.22

# monitor.sh needs: bash (script), curl (probes + ntfy alerts), openssl
# (TLS expiry check), python3 (JSON validation + threshold math),
# openssh-client (VPS internal checks), tzdata (date matches festival TZ).
RUN apk add --no-cache bash curl openssl python3 openssh-client tzdata

WORKDIR /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# monitor.sh itself is bind-mounted at runtime (see docker-compose.yml), so
# script updates never require an image rebuild.
ENTRYPOINT ["/entrypoint.sh"]
