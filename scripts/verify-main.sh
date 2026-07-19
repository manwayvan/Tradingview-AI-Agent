#!/usr/bin/env bash
# Fail if the remote has any head other than main, or if we are not on main.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== verify main-only branch policy ==="

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${branch}" != "main" ]]; then
  echo "  ✗ current branch is '${branch}' (expected main)"
  exit 1
fi
echo "  ✓ on main"

git fetch origin --prune --quiet

mapfile -t heads < <(git ls-remote --heads origin | awk '{print $2}' | sed 's#refs/heads/##')
if [[ "${#heads[@]}" -eq 0 ]]; then
  echo "  ✗ no remote heads found"
  exit 1
fi

extra=()
for h in "${heads[@]}"; do
  if [[ "${h}" != "main" ]]; then
    extra+=("${h}")
  fi
done

if [[ "${#extra[@]}" -gt 0 ]]; then
  echo "  ✗ unexpected remote branches (delete them): ${extra[*]}"
  echo "    git push origin --delete ${extra[*]}"
  exit 1
fi
echo "  ✓ origin has only main"

if git rev-parse --verify origin/main >/dev/null 2>&1; then
  local_sha="$(git rev-parse HEAD)"
  remote_sha="$(git rev-parse origin/main)"
  if [[ "${local_sha}" != "${remote_sha}" ]]; then
    echo "  ⚠ local main (${local_sha:0:7}) differs from origin/main (${remote_sha:0:7})"
  else
    echo "  ✓ local main matches origin/main"
  fi
fi

echo ""
echo "Branch policy OK. See docs/BRANCH_POLICY.md"
