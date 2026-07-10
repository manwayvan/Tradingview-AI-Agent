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
| **VPS** (Hetzner, DigitalOcean) | Full control | ~$4–6/mo, run `docker compose` |

Any of these can run:

```bash
uvicorn optionsagents.webhook_server:app --host 0.0.0.0 --port $PORT
```

with a persistent volume for `~/.tradingagents`.

## If you still want Netlify

Use Netlify **only** for a marketing/landing page (static HTML). Host the **API
elsewhere** (Railway/Render) and set `OPTIONS_PUBLIC_URL` to that API domain.

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
5. Or use a `production` branch: only merges to `production` trigger Netlify

### Better: skip Netlify for the app entirely

Point a custom domain at Railway/Render instead. You get:

- Unlimited local testing
- One production deploy when you choose
- No rewrite for background jobs / SQLite

## Production checklist

```bash
# On your host (Railway/Render/Fly/VPS)
export OPTIONS_PUBLIC_URL=https://your-domain.com
export OPTIONS_COOKIE_SECURE=true
# LLM keys, etc. from .env.example

python run_options.py serve --host 0.0.0.0 --port $PORT
```

- [ ] HTTPS on port 443 (required by TradingView)
- [ ] Persistent disk for `~/.tradingagents` (user DB + paper accounts)
- [ ] `OPTIONS_PUBLIC_URL` set to your public URL
- [ ] TradingView: paid plan + 2FA enabled
- [ ] Process manager or platform health checks (so the engine stays running)

## Docker production (optional)

```bash
docker build -t options-ai-agent .
docker run -p 8000:8000 --env-file .env \
  -v options_data:/home/appuser/.tradingagents \
  options-ai-agent uvicorn optionsagents.webhook_server:app --host 0.0.0.0 --port 8000
```

(Override the default `ENTRYPOINT` for web serve — see `Dockerfile`.)

## Summary

| Goal | Solution |
|------|----------|
| Test without waiting / without deploy quota | `make dev` locally |
| Test TradingView webhooks without deploy | `make tunnel` |
| Run CI-quality checks before commit | `make test` |
| Public web app that actually works | Railway / Render / Fly / VPS — **not Netlify** |
| Save Netlify minutes | Don’t auto-deploy; or use Netlify only for static marketing site |
