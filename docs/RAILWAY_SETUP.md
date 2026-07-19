# Railway setup — after connecting GitHub

Connect Railway to the **`main`** branch only.  
Branch policy: [BRANCH_POLICY.md](./BRANCH_POLICY.md)

You connected the repo. Finish these steps in the [Railway dashboard](https://railway.app/dashboard) so the app stays up and keeps user data.

**Known project (this repo):**

| | |
|--|--|
| Railway project | [appealing-energy](https://railway.com/project/01f60eda-6cde-4506-b8a8-bd9d3a827670) |
| Service | `Tradingview-AI-Agent` |
| Public URL | `https://moneymaker9000.up.railway.app` |

If that URL returns Railway’s JSON `Application not found`, the service is not
serving traffic — open the project → **Deployments → Redeploy** from `main`
(or push a commit to `main` and wait for the GitHub commit status
`appealing-energy - Tradingview-AI-Agent`).

## 0. Source branch

**Settings → Source → Branch:** `main`  
(Root directory: `/`, Dockerfile: `Dockerfile.web`)

## 1. Confirm build settings

Railway should pick up `railway.toml` automatically:

- **Builder:** Dockerfile  
- **Dockerfile path:** `Dockerfile.web`

If the first deploy failed, open **Settings → Build** and set Dockerfile path to `Dockerfile.web`.

## 2. Add a persistent volume (required)

Without this, accounts reset on every redeploy.

1. Open your service → **Volumes** → **Add Volume**
2. **Mount path:** `/data`
3. Save

## 3. Set environment variables

**Variables** tab → add these (use **your** Railway public URL):

| Variable | Value | Required |
|----------|--------|----------|
| `OPTIONS_DATA_DIR` | `/data` | Yes |
| `OPTIONS_PUBLIC_URL` | `https://moneymaker9000.up.railway.app` | Yes |
| `OPTIONS_COOKIE_SECURE` | `true` | Yes |
| `OPENAI_API_KEY` | your key | Yes (or Anthropic/Google) |
| `AUTONOMOUS_ENABLED` | `false` | Optional (enable from UI later) |

Copy the public URL from **Settings → Networking → Generate Domain** if you have not already.

Template: `.env.production.example`

## 4. Redeploy

After volume + variables: **Deployments → Redeploy** (or push a commit).

Watch **Build logs** then **Deploy logs**. Success looks like:

```
Uvicorn running on http://0.0.0.0:XXXX
```

## 5. Smoke test

Replace with your Railway URL (this project’s default shown):

```bash
curl https://moneymaker9000.up.railway.app/health
# → {"status":"ok","users":0,...}

open https://moneymaker9000.up.railway.app/signup
```

Create an account → open **TV** tab → copy webhook URL for TradingView.

## 6. TradingView webhook

Webhook URL:

```
https://moneymaker9000.up.railway.app/webhook/tradingview
```

Each user’s **personal secret** is on the TV tab after sign-in (embedded in the Pine script). TradingView needs paid plan + 2FA.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Application not found` on `*.up.railway.app` | Service offline or domain removed — Redeploy from `main`; re-generate domain under Settings → Networking if needed |
| Build fails “Dockerfile not found” | Set Dockerfile path to `Dockerfile.web` |
| Health check failing | Wait 30s after start; check deploy logs for Python errors |
| 502 / crash on start | Add LLM API key; check logs for import errors |
| Sign-in works but data lost on redeploy | Volume at `/data` + `OPTIONS_DATA_DIR=/data` |
| Have to create a new account every visit | Same as above — DB wiped without volume; check `/health` → `persistence.warning` |
| Session expired but account exists | Use **Sign in** (not Sign up) with same email; sessions now slide on activity |
| TV wizard shows `localhost` URL | Set `OPTIONS_PUBLIC_URL` to Railway HTTPS URL |
| Lovable UI can’t call API | Set `OPTIONS_CORS_ORIGINS` to your Lovable preview URL |
| Pushes to `main` do not deploy | Settings → Source → Branch must be `main`; confirm GitHub app still connected |

## Local vs Railway

- **Develop locally:** `make dev` (no Railway deploy needed)
- **Ship to Railway:** push to GitHub only when `make deploy-check` passes

## Optional: custom domain

**Settings → Networking → Custom Domain** → point DNS → update `OPTIONS_PUBLIC_URL` to match.
