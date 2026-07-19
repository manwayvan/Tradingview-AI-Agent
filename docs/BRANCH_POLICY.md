# Branch policy — `main` only

This repository uses a **single branch workflow**. There are no long-lived feature
branches and no GitHub forks.

| Branch | Purpose |
|--------|---------|
| **`main`** | Only branch. Production-ready code. Railway deploys from here. |

## Current remote state (verified)

```bash
git ls-remote --heads origin
# refs/heads/main   ← only head
```

- Default branch: `main`
- Remote forks: **none** (`forks_count: 0`)
- Open PRs: **none**
- Legacy `cursor/*` and `claude/*` heads: **removed**

## Railway

In Railway → **Settings → Source**:

- **Branch:** `main` (required)
- **Dockerfile:** `Dockerfile.web`
- **Root directory:** `/`

Project: [appealing-energy](https://railway.com/project/01f60eda-6cde-4506-b8a8-bd9d3a827670)  
Service: `moneymaker9000`  
Public URL (after domain is active): `https://moneymaker9000-production.up.railway.app`

Every push to `main` should trigger a Railway deploy. Confirm with:

```bash
gh api repos/manwayvan/Tradingview-AI-Agent/commits/main/status \
  --jq '.statuses[] | select(.context|test("railway|moneymaker";"i"))'
# expect state=success and description containing moneymaker9000-production.up.railway.app

curl -sS https://moneymaker9000-production.up.railway.app/health
# → {"status":"ok", ...}
```

If health returns Railway’s `Application not found`, the service is offline — open
the Railway dashboard → **Deployments → Redeploy** (or push a commit to `main`).
See [RAILWAY_SETUP.md](./RAILWAY_SETUP.md).

## Local development

```bash
git checkout main
git pull origin main
make dev
```

Do **not** create feature branches or forks for routine work. Commit and push
directly to `main` after `make deploy-check` (or at least `make test`) passes.

## Verify before shipping

```bash
make verify-main   # only origin/main exists; working tree on main
make deploy-check  # tests (+ docker build when docker is available)
```
