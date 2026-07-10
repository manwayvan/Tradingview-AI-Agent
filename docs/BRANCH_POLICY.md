# Branch policy — `main` only

This repository uses a **single branch workflow**:

| Branch | Purpose |
|--------|---------|
| **`main`** | Production-ready code. Railway deploys from here. All merges go to `main`. |

## Railway

In Railway → **Settings → Source**:

- **Branch:** `main`
- **Dockerfile:** `Dockerfile.web`

## Local development

```bash
git checkout main
git pull origin main
make dev
```

Do not create long-lived feature branches unless you need a PR review — merge back to `main` when done.

## One-time GitHub cleanup (repo owner)

The cloud agent token cannot change the default branch (requires repo **Admin**). Do this once in the GitHub UI:

1. GitHub → **Settings → General → Default branch** → switch to **`main`**
2. Then delete the legacy branch (from your machine or any shell with push access):

```bash
git push origin --delete claude/options-trading-agent-k4y8cr
```

**Already removed:** all `cursor/*` feature branches on the remote. There are **no GitHub forks** of this repository.

After cleanup, only `main` should remain:

```bash
git ls-remote --heads origin
# refs/heads/main
```
