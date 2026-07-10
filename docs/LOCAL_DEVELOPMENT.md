# Local development (test everything before deploying)

Use this workflow to run and validate the **full application on your machine** —
web UI, user accounts, autonomous AI, strategies, and TradingView webhooks — without
pushing to Git or burning Netlify deploy quota.

## Quick start (recommended)

```bash
# 1. One-time setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # add your LLM key(s)
cp .env.local.example .env.local   # optional local overrides

# 2. Run with hot reload (code changes apply without restart)
make dev
# or: python run_options.py dev

# 3. Open in browser
#    http://localhost:8000
```

In another terminal:

```bash
# Run the full test suite (no network, ~1 min)
make test

# Quick smoke check that the server is up
make smoke
```

## What “fully local” covers

| Feature | How to test locally |
|---------|---------------------|
| Sign up / sign in | http://localhost:8000/signup |
| Dashboard (mobile + desktop) | http://localhost:8000/app |
| Autonomous AI brain | AI tab → enable / run cycle |
| Strategies | Plans tab → add strategy |
| TradingView setup wizard | TV tab → copy URL, secret, Pine |
| TradingView **live** webhooks | `make tunnel` (see below) |
| CLI | `python run_options.py analyze NVDA` |

User data is stored under `~/.tradingagents/` (SQLite `app.db`, per-user paper accounts).

## Hot reload dev server

```bash
python run_options.py dev --port 8000
```

This runs Uvicorn with `--reload` on the `optionsagents/` package. Edit Python or
static files (HTML/CSS/JS) and refresh the browser — no git commit, no deploy.

Optional flags:

```bash
python run_options.py dev --autonomous    # start with AI brain enabled
python run_options.py dev --port 9000
```

## Test TradingView webhooks locally (no deploy)

TradingView must reach a **public HTTPS** URL. For local dev, use a tunnel — you
do **not** need Netlify for this.

```bash
# Terminal 1
make dev

# Terminal 2 — free Cloudflare quick tunnel (no account required)
make tunnel
```

The tunnel script prints an `https://….trycloudflare.com` URL. Put that in the TV
tab as your public URL (or set `OPTIONS_PUBLIC_URL` in `.env.local` to that URL
so the wizard copies the right webhook link).

Then in TradingView: paste `https://<tunnel>/webhook/tradingview` as the alert
webhook URL.

**Alternatives:** [ngrok](https://ngrok.com/) (`ngrok http 8000`), [localtunnel](https://localtunnel.github.io/www/).

## Docker (optional, same as local)

```bash
make docker-dev
```

Uses `docker-compose.dev.yml` — mounts your source code, enables reload, persists
data in a Docker volume.

## Recommended git → test → deploy flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  Edit locally   │ ──► │  make test       │ ──► │  make dev + browser │
│  (no git push)  │     │  make smoke      │     │  make tunnel (TV)   │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
                                                          │
                                                          ▼
                                               Happy? git commit + push
                                               Deploy ONCE to production host
```

**Do not** connect Netlify auto-deploy to every push while you are still iterating.
See [DEPLOYMENT.md](./DEPLOYMENT.md) for why and what to use instead.

## Environment files

| File | Purpose |
|------|---------|
| `.env` | Shared secrets (LLM keys) — usually not committed |
| `.env.local` | Machine-specific overrides (port, tunnel URL) — gitignored |
| `.env.local.example` | Template for `.env.local` |

`.env.local` is loaded automatically by `make dev` and overrides `.env`.

## Troubleshooting

**Port in use:** `PORT=9000 make dev`

**Database reset (fresh accounts):** `rm ~/.tradingagents/app.db`

**Webhook 401:** secret in Pine script must match the TV tab on the same account

**LLM errors:** set at least one of `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` in `.env`

**Mobile testing on same Wi‑Fi:** open `http://<your-lan-ip>:8000` on your phone (HTTP only; tunnel needed for TradingView)
