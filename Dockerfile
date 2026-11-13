# One image serves the API, the dashboard and a headless Chromium for UI tests.
#   docker build -t uatpilot .
#   docker run -p 8000:8000 uatpilot            # http://localhost:8000

# --- 1. Build the dashboard as a static site --------------------------------------------------
FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Same-origin API: the UI calls /api/* on whatever host serves it.
ENV STATIC_EXPORT=1 NEXT_PUBLIC_API_URL="" NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# --- 2. Python runtime + Chromium -------------------------------------------------------------
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install ".[ui]" \
    && python -m playwright install --with-deps chromium \
    && rm -rf /root/.cache /var/lib/apt/lists/*

COPY examples/ ./examples/
COPY --from=web /web/out /app/static

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

# Defaults for a public deployment; override at runtime. With UATPILOT_WRITE_TOKEN set, the
# dashboard is read-only for visitors (generation and runs cost money / hit the sandboxes).
ENV UATPILOT_STATIC_DIR=/app/static \
    UATPILOT_DB=/data/uatpilot.db \
    UATPILOT_ARTIFACTS=/data/artifacts \
    UATPILOT_ENGINE=auto \
    PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health', timeout=4)"

# One worker: the "one suite run at a time" lock and the rate limiter live in process memory.
CMD ["sh", "-c", "uatpilot seed --dir /app/examples/suites && exec uvicorn uatpilot.api:create_app --factory --host 0.0.0.0 --port ${PORT} --workers 1 --no-server-header"]
