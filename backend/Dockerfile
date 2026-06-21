# --- Web UI build stage ---
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Runtime ---
FROM python:3.12-slim
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# The built SPA (served by FastAPI at "/").
COPY --from=web /web/dist ./web

# Run as a non-root user. /data is the default DB + .jwt_secret location and must
# be writable by this uid (use the same uid 1000 for bind mounts).
RUN useradd -m -u 1000 app && mkdir -p /data && chown -R app:app /srv /data
USER app

EXPOSE 5056

# Bare `docker run` / orchestrators get a real liveness signal (compose sets one too).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','5056')+'/api/health').status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-5056}"]
