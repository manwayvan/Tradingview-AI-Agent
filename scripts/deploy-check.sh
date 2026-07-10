#!/usr/bin/env bash
# Pre-deploy checklist — run locally before pushing to production.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Options AI Agent — deploy check ==="
echo ""

fail=0

check() {
  if "$@"; then
    echo "  ✓ $1"
  else
    echo "  ✗ $1"
    fail=1
  fi
}

echo "Tests"
check python3 -m pytest tests/ -q --ignore=tests/test_vendor_errors.py

echo ""
echo "Docker production image"
if command -v docker >/dev/null 2>&1; then
  docker build -f Dockerfile.web -t options-ai-agent:check . >/dev/null
  echo "  ✓ docker build Dockerfile.web"
else
  echo "  ⚠ docker not installed — skip image build"
fi

echo ""
echo "Required production env (set on host)"
for var in OPTIONS_PUBLIC_URL OPTIONS_DATA_DIR; do
  if [[ -n "${!var:-}" ]]; then
    echo "  ✓ ${var}=${!var}"
  else
    echo "  ⚠ ${var} not set in this shell (set on Railway/Render/Fly)"
  fi
done

if [[ -z "${OPENAI_API_KEY:-}${ANTHROPIC_API_KEY:-}${GOOGLE_API_KEY:-}" ]]; then
  echo "  ⚠ No LLM API key in shell — set one on production host"
fi

echo ""
if [[ "${fail}" -eq 0 ]]; then
  echo "Ready to deploy. See docs/DEPLOYMENT.md and docs/LOVABLE.md"
else
  echo "Fix failures above before deploying."
  exit 1
fi
