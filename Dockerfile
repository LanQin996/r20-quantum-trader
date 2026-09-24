# ==========================================
# Stage 1: Build Vue 3 Frontend
# ==========================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

# The frontend keeps its legacy /images path as a symlink to the shared docs assets.
# Copy the link target into the builder at the same relative location before Vite scans public/.
COPY docs/ /app/docs/
COPY frontend/ ./
RUN npm run build

# ==========================================
# Stage 2: Python Runtime & Backend Engine
# ==========================================
FROM node:20-bookworm-slim AS node-runtime

FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    R20_DOCKER=1 \
    PYTHONPATH=/app \
    TZ=Asia/Shanghai \
    PORT=8080

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Node.js + official OKX CLI (required by the trading/gateway engine)
COPY --from=node-runtime /usr/local/bin /usr/local/bin
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN npm install -g "@okx_ai/okx-trade-cli@1.4.4" && okx --version

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy Backend, Gateway, Scripts, Plugins and Assets
COPY r20_backend/ ./r20_backend/
COPY r20_gateway/ ./r20_gateway/
COPY scripts/ ./scripts/
COPY fcntl_compat.py ./fcntl_compat.py
COPY plugins/ ./plugins/
COPY dashboard/ ./dashboard/
COPY docs/ ./docs/
COPY deploy/ ./deploy/
COPY env.example ./env.example

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Create non-root runtime user and ensure writable runtime state folders exist.
# /app is chowned so the backend can atomically rewrite /app/.env when saving config.
RUN useradd --create-home --shell /bin/bash r20 && \
    mkdir -p /app/data /app/logs /app/backups && \
    chmod +x /app/deploy/docker-entrypoint.sh && \
    chown -R r20:r20 /app

USER r20

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/api/v1/health || exit 1

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["backend"]
