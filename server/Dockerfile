FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ffmpeg libvips-dev build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py chat_api.py chat_ws.py chat_db.py chat_moderation.py ./
COPY chat/ ./chat/
EXPOSE 8080
RUN useradd -r -u 1001 -s /sbin/nologin appuser && \
    chown -R appuser:appuser /app && \
    apt-get update && apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
# Caddy (on the same Docker network) is the only ingress; it strips and re-sets forwarded headers.
# Access log and uvicorn INFO are OFF: request paths contain secrets (magic-link
# tokens in /chat/v/{token}, session tokens in /ws/chat/{token}) and must never
# be logged. App-level logging (basicConfig INFO: [MOD], push, VAPID) is separate
# and unaffected.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--log-level", "warning"]
