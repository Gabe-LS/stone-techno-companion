FROM alpine:3.22

# monitor.sh needs: bash (script), curl (probes + ntfy alerts + GitHub
# fetch), openssl (TLS expiry check), python3 (JSON validation + threshold
# math), openssh-client (VPS internal checks), tzdata (date matches
# festival TZ).
RUN apk add --no-cache bash curl openssl python3 openssh-client tzdata

WORKDIR /app

COPY entrypoint.sh /entrypoint.sh
COPY run-monitor.sh /app/run-monitor.sh
RUN chmod +x /entrypoint.sh /app/run-monitor.sh

ENTRYPOINT ["/entrypoint.sh"]
