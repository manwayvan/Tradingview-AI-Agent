# Deployment guide (and why not Netlify for the full app)

## The short answer

**Do not deploy this entire application to Netlify.** Netlify is built for static
sites and short-lived serverless functions. This project is a **long-running
Python server** with:

- SQLite database and per-user file storage
- Background threads (strategy engine, autonomous AI loop)
- TradingView webhook endpoints that enqueue work

That architecture needs a **container or VPS**, not Netlify’s static + Functions model.

Deploying here will hit walls beyond build minutes: functions time out (~10–26s),
no persistent local disk for SQLite, no background schedulers, cold starts on
every request.

## What to use instead (all have generous free tiers)

| Platform | Good for | Free tier notes |
|----------|----------|-----------------|
| **[Railway](https://railway.app)** | Easiest Docker deploy | ~$5 credit/month, one-click from GitHub |
| **[Render](https://render.com)** | Simple web service | Free web service (sleeps after idle) |
| **[Fly.io](https://fly.io)** | Always-on, global | Small VMs free tier |
| **[Lovable](https://lovable.dev)** | React UI (optional) | Pair with Railway backend — see [LOVABLE.md](./LOVABLE.md) |
| **VPS** (Hetzner, DigitalOcean) | Full control | ~$4–6/mo, run `docker compose` |

Any of these can run:

```bash
uvicorn optionsagents.webhook_server:app --host 0.0.0.0 --port $PORT
```

with a persistent volume for `~/.tradingagents`.

## If you still want Netlify

Use Netlify **only** for a marketing/landing page (static HTML). Host the **API
elsewhere** (Railway/Render) and set `OPTIONS_PUBLIC_URL` to that API domain.

This repo already ships that setup: `netlify.toml` + `netlify/public/` redirect
to Railway. **Production branch must be `main`** — see [NETLIFY.md](./NETLIFY.md).
If Netlify still references `claude/options-trading-agent-k4y8cr`, update the
site setting; do not recreate that branch.

A single-repo “full app on Netlify” would require rewriting the backend as
serverless functions + external database (Postgres) + external job queue — a
large architectural change, not a config tweak.

## Netlify build-minute problem (your concern)

Netlify free tier limits **build minutes per month**. Pushing to `main` on every
small fix burns that quota fast.

### Strategy: local-first, deploy rarely

1. **Develop 100% locally** — [LOCAL_DEVELOPMENT.md](./LOCAL_DEVELOPMENT.md)
2. **Run `make test` before every commit**
3. **Disable** Netlify “deploy on every push” while iterating
4. **Deploy manually** when a version is ready (Netlify dashboard → Trigger deploy)

This repo stays on **`main` only** for the real app (Railway). Do not add a
second “production” branch — see [BRANCH_POLICY.md](./BRANCH_POLICY.md).

### Better: skip Netlify for the app entirely

Point a custom domain at Railway/Render instead. You get:

- Unlimited local testing
- One production deploy from `main` when you choose
- No rewrite for background jobs / SQLite

## One-command production files

| File | Platform |
|------|----------|
| `Dockerfile.web` | Docker (all platforms) |
| `railway.toml` | [Railway](https://railway.app) |
| `render.yaml` | [Render](https://render.com) Blueprint |
| `fly.toml` | [Fly.io](https://fly.io) |
| `.env.production.example` | Secret template |

Pre-flight: `make deploy-check`

**Lovable users:** deploy the Python API to Railway, optionally rebuild the UI in
Lovable — full guide in [LOVABLE.md](./LOVABLE.md).

## Production checklist

```bash
# On your host (Railway/Render/Fly/VPS)
export OPTIONS_DATA_DIR=/data          # mount persistent volume here
export OPTIONS_PUBLIC_URL=https://your-domain.com
export OPTIONS_COOKIE_SECURE=true
# LLM keys, etc. from .env.production.example

# Docker (recommended on Railway/Render):
# Uses Dockerfile.web automatically via railway.toml / render.yaml
```

- [ ] HTTPS on port 443 (required by TradingView)
- [ ] Persistent disk for `~/.tradingagents` (user DB + paper accounts)
- [ ] `OPTIONS_PUBLIC_URL` set to your public URL
- [ ] TradingView: paid plan + 2FA enabled
- [ ] Process manager or platform health checks (so the engine stays running)

## Docker production

```bash
docker build -f Dockerfile.web -t options-ai-agent .
docker run -p 8000:8000 --env-file .env.production.example \
  -e OPTIONS_DATA_DIR=/data \
  -v options_data:/data \
  options-ai-agent
```

## Summary

| Goal | Solution |
|------|----------|
| Test without waiting / without deploy quota | `make dev` locally |
| Test TradingView webhooks without deploy | `make tunnel` |
| Run CI-quality checks before commit | `make test` |
| Public web app that actually works | Railway / Render / Fly — see `make deploy-check` |
| Polished React UI | Lovable frontend + Railway API — [LOVABLE.md](./LOVABLE.md) |
| Save Netlify minutes | Don’t auto-deploy; or use Netlify only for static marketing site |
