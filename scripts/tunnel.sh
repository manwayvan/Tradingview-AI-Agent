#!/usr/bin/env bash
# Expose localhost:8000 via HTTPS for TradingView webhook testing (no Netlify deploy).
set -euo pipefail

PORT="${PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"

if ! curl -sf "${BASE}/health" >/dev/null 2>&1; then
  echo "Error: nothing listening on ${BASE}" >&2
  echo "Start the app first: make dev" >&2
  exit 1
fi

echo "Local app is up at ${BASE}"
echo "Starting HTTPS tunnel (TradingView can reach this URL)..."
echo ""

if command -v cloudflared >/dev/null 2>&1; then
  echo "Using cloudflared (free, no account required for quick tunnels)"
  echo "Set OPTIONS_PUBLIC_URL in .env.local to the https URL below for the TV wizard."
  echo ""
  exec cloudflared tunnel --url "${BASE}"
fi

if command -v ngrok >/dev/null 2>&1; then
  echo "Using ngrok"
  exec ngrok http "${PORT}"
fi

if command -v lt >/dev/null 2>&1; then
  echo "Using localtunnel"
  exec lt --port "${PORT}"
fi

echo "No tunnel tool found. Install one of:" >&2
echo "  cloudflared — https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" >&2
echo "  ngrok       — https://ngrok.com/download" >&2
echo "  localtunnel — npm install -g localtunnel" >&2
exit 1
