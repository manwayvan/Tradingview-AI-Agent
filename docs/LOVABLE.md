# Deploying with Lovable (+ this Python backend)

Lovable is excellent for **shipping a polished React UI quickly**. This repo’s
**trading engine** (AI agents, paper broker, webhooks, background schedulers) stays
in Python and should run on **Railway, Render, or Fly** — not inside Lovable’s
static/serverless runtime.

Use **local dev for daily work**, then deploy when ready — you won’t burn Lovable
or Netlify quotas while iterating.

## Recommended architecture

```
┌─────────────────────┐         HTTPS API          ┌──────────────────────────┐
│  Lovable (optional) │  ───────────────────────►  │  Railway / Render / Fly  │
│  React dashboard    │      cookies + /api/*      │  Python FastAPI app      │
│  lovable.app URL    │                            │  SQLite + engines on /data │
└─────────────────────┘                            └───────────┬──────────────┘
                                                               │
                                                    TradingView webhooks
                                                    POST /webhook/tradingview
```

### Path A — Simplest (no Lovable UI work)

Deploy **this entire repo** with `Dockerfile.web`. The built-in mobile web app
(`/app`, `/login`) is already included.

1. `make deploy-check` locally
2. Push to GitHub
3. Railway → New Project → Deploy from repo → uses `railway.toml` + `Dockerfile.web`
4. Add volume mount `/data`, set env from `.env.production.example`
5. Set `OPTIONS_PUBLIC_URL` to your Railway URL

**You can still use Lovable** later for a marketing landing page that links to your app.

### Path B — Lovable frontend + Python API (hybrid)

1. Deploy backend to Railway (Path A steps 1–5)
2. In Lovable: create a project using `lovable/PROJECT_BRIEF.md` as the spec
3. Set Lovable env: `VITE_API_URL=https://your-railway-app.up.railway.app`
4. On Railway, set:
   ```bash
   OPTIONS_CORS_ORIGINS=https://your-project.lovable.app,https://your-custom-domain.com
   ```
5. Lovable `deploy_project` publishes the UI; API stays on Railway

### Path C — Lovable Cloud database (future)

Today user accounts use **SQLite** on a persistent volume (`OPTIONS_DATA_DIR`).
Lovable Cloud Postgres is optional for a future migration if you need multi-region
or heavy analytics — not required to ship v1.

## Local workflow (before any Lovable/Railway deploy)

```bash
make dev          # full app at localhost:8000
make test         # before git push
make tunnel       # TradingView webhooks without deploy
make deploy-check # before production push
```

See [LOCAL_DEVELOPMENT.md](./LOCAL_DEVELOPMENT.md).

## Deploy backend to Railway (step-by-step)

1. **Test locally:** `make test && make dev`
2. **Push** your branch to GitHub
3. [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
4. Railway detects `railway.toml` and `Dockerfile.web`
5. **Add volume:** Settings → Volumes → mount `/data`
6. **Variables** (from `.env.production.example`):
   - `OPTIONS_DATA_DIR=/data`
   - `OPTIONS_PUBLIC_URL=https://<your-service>.up.railway.app`
   - `OPTIONS_COOKIE_SECURE=true`
   - `OPENAI_API_KEY` (or other LLM key)
7. **Deploy** → open URL → `/signup`

TradingView webhook URL: `https://<your-service>.up.railway.app/webhook/tradingview`

## Deploy to Render

1. Connect repo → **New Blueprint** → select `render.yaml`
2. Set secret env vars in dashboard
3. Render attaches a 1GB disk at `/data` automatically (per blueprint)

## Using Lovable MCP from Cursor

Once Lovable is authenticated in Cursor:

1. `create_project` or `list_projects` to find your UI project
2. Paste the contents of `lovable/PROJECT_BRIEF.md` via `send_message`
3. Set project knowledge: API base URL = your Railway domain
4. `get_diff` after each change to verify
5. `deploy_project` when the UI is ready — **backend deploy is separate** (Railway)

## CORS (Lovable → Railway)

If the UI is on a different domain than the API, set on the **backend**:

```bash
OPTIONS_CORS_ORIGINS=https://id-preview--xxxx.lovable.app,https://your-app.lovable.app
```

The bundled HTML app (Path A) does **not** need CORS — same origin.

## What not to do

| Don't | Why |
|-------|-----|
| Put Python backend on Netlify | No long-running processes / SQLite |
| Auto-deploy on every git push while learning | Wastes build minutes; use `make dev` |
| Skip `OPTIONS_DATA_DIR` volume | User accounts reset on every deploy |
| Forget `OPTIONS_PUBLIC_URL` | TradingView wizard shows wrong webhook URL |

## Quick reference

| Task | Command / file |
|------|----------------|
| Local dev | `make dev` |
| Pre-deploy tests | `make deploy-check` |
| Railway config | `railway.toml`, `Dockerfile.web` |
| Render config | `render.yaml` |
| Fly config | `fly.toml` |
| Lovable UI brief | `lovable/PROJECT_BRIEF.md` |
| Production env | `.env.production.example` |
