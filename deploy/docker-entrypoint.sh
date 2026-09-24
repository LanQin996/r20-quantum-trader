#!/usr/bin/env bash
set -e

# ==============================================================================
# R20 Quantum Trader - Container Entrypoint
# ==============================================================================

ROOT_DIR="/app"
cd "$ROOT_DIR"

# 1. 确保必要运行时目录存在
mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/logs" "$ROOT_DIR/backups"

# 2. Compose 通过 env_file 注入配置；保留可原子改写的容器内配置文件。
if [ -d "$ROOT_DIR/.env" ]; then
    echo "⚠️ [Entrypoint] Warning: /app/.env is mounted as a directory! Please mount a file instead."
elif [ ! -f "$ROOT_DIR/.env" ] && [ "${R20_DOCKER:-}" = "1" ]; then
    # 示例值会覆盖已注入的环境变量，因此容器模式只创建空覆盖文件。
    touch "$ROOT_DIR/.env"
    chmod 600 "$ROOT_DIR/.env"
elif [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/env.example" ]; then
    echo "⚠️ [Entrypoint] .env not found. Generating default .env from env.example..."
    cp "$ROOT_DIR/env.example" "$ROOT_DIR/.env"
    chmod 600 "$ROOT_DIR/.env"
fi

# 3. 初始化默认标的池（如果不存在，避免冷启动阻断）
if [ ! -f "$ROOT_DIR/data/instrument_pool.json" ]; then
    echo "📋 [Entrypoint] Initializing default instrument pool in data/..."
    python3 -c "from scripts.instrument_pool import save_instruments, DEFAULT_INSTRUMENTS; save_instruments(DEFAULT_INSTRUMENTS)" 2>/dev/null || true
fi

MODE="${1:-backend}"

case "$MODE" in
    backend|web|all)
        # FastAPI lifespan 已负责启动并守护网关 worker。
        echo "✨ [R20] Starting Web Engine & Control Plane on 0.0.0.0:8080..."
        exec python3 -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
        ;;
    gateway|worker)
        echo "🚀 [R20] Starting Quantitative Gateway & Dispatch Worker..."
        exec python3 -m r20_gateway.worker
        ;;
    *)
        exec "$@"
        ;;
esac
