#!/usr/bin/env bash
# Start the web app locally with hot reload. No git push, no Netlify deploy.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then set -a; source .env; set +a; fi
if [[ -f .env.local ]]; then set -a; source .env.local; set +a; fi

export OPTIONS_PUBLIC_URL="${OPTIONS_PUBLIC_URL:-http://localhost:${PORT:-8000}}"

echo "→ Local dev server: ${OPTIONS_PUBLIC_URL}"
echo "→ Sign up: ${OPTIONS_PUBLIC_URL}/signup"
echo "→ App:     ${OPTIONS_PUBLIC_URL}/app"
echo "→ Tests:   make test   (another terminal)"
echo "→ Tunnel:  make tunnel (TradingView webhooks without deploy)"
echo ""

PYTHON=python3
[[ -x .venv/bin/python ]] && PYTHON=.venv/bin/python

exec "${PYTHON}" run_options.py dev --host 0.0.0.0 --port "${PORT:-8000}" "$@"
