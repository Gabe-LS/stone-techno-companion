FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ffmpeg libvips-dev && rm -rf /var/lib/apt/lists/*
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
# Caddy (on the same Docker network) is the only ingress; it strips and re-sets forwarded headers
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
