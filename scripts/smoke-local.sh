#!/usr/bin/env bash
# Quick smoke test — run while `make dev` is up.
set -euo pipefail

PORT="${PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"

echo "Smoke testing ${BASE} ..."

health=$(curl -sf "${BASE}/health")
echo "  /health OK — ${health}"

code=$(curl -sf -o /dev/null -w "%{http_code}" "${BASE}/login")
[[ "${code}" == "200" ]] || { echo "  /login failed (${code})"; exit 1; }
echo "  /login OK"

code=$(curl -sf -o /dev/null -w "%{http_code}" "${BASE}/signup")
[[ "${code}" == "200" ]] || { echo "  /signup failed (${code})"; exit 1; }
echo "  /signup OK"

# App redirects unauthenticated users; API should 401 without session
code=$(curl -sf -o /dev/null -w "%{http_code}" "${BASE}/api/state" || true)
[[ "${code}" == "401" ]] || { echo "  /api/state expected 401, got ${code}"; exit 1; }
echo "  /api/state auth gate OK (401 without login)"

echo ""
echo "All smoke checks passed."
