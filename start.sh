#!/usr/bin/env bash
# ==============================================================================
# R20 Quantum Trader - Quick Start Script
# ==============================================================================

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "🚀 [R20 Quantum Trader] Initializing system environment..."

# 1. Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is not installed."
    exit 1
fi

# 2. Check or create .env
if [ ! -f .env ]; then
    if [ -f env.example ]; then
        echo "📝 Creating .env from env.example..."
        cp env.example .env
    else
        echo "⚠️ Warning: env.example not found, please configure .env manually."
    fi
fi

# 3. Warn loudly if key secrets are still empty/placeholder (demo/read-only mode still works)
if [ -f .env ]; then
    placeholder_found=""
    for key in OKX_DEMO_API_KEY OKX_DEMO_SECRET_KEY OKX_DEMO_PASSPHRASE \
               OKX_LIVE_API_KEY OKX_LIVE_SECRET_KEY OKX_LIVE_PASSPHRASE \
               OKX_API_KEY OKX_SECRET_KEY OKX_PASSPHRASE LLM_API_KEY; do
        value="$(grep -E "^${key}=" .env | head -n1 | cut -d= -f2- | tr -d '"'"'"' ')"
        if [ -n "$(grep -E "^${key}=" .env)" ] && { [ -z "$value" ] || [[ "$value" == your_* ]] || [[ "$value" == *placeholder* ]] || [[ "$value" == *changeme* ]]; }; then
            placeholder_found="$placeholder_found $key"
        fi
    done
    if [ -n "$placeholder_found" ]; then
        echo "⚠️⚠️⚠️  WARNING: the following secrets in .env are EMPTY or PLACEHOLDER values:$placeholder_found" >&2
        echo "⚠️⚠️⚠️  Live trading and LLM features will NOT work until real credentials are configured." >&2
        echo "⚠️⚠️⚠️  The system will continue starting in demo/read-only mode. Edit .env to enable full functionality." >&2
    fi
fi

# 4. Create required runtime directories
mkdir -p data logs backups

# 5. Check Node.js and build frontend if dist doesn't exist
if [ ! -d "frontend/dist" ]; then
    echo "📦 Frontend production bundle not detected. Building Vue 3 SPA..."
    if command -v npm &> /dev/null; then
        cd frontend
        npm install
        npm run build
        cd "$ROOT_DIR"
    else
        echo "⚠️ Warning: npm is not installed. Please build frontend manually via 'cd frontend && npm install && npm run build'."
    fi
fi

# 6. Start Backend Engine
echo "✨ Launching R20 Quantum Trader on http://0.0.0.0:8080 ..."
exec python3 -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
