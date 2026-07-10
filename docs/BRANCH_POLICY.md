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

If the repo default is still `claude/options-trading-agent-k4y8cr`:

1. GitHub → **Settings → General → Default branch** → switch to **`main`**
2. Delete the old branch: `git push origin --delete claude/options-trading-agent-k4y8cr`

Both branches currently point to the same commit (`550572c`); `main` is the canonical name going forward.
