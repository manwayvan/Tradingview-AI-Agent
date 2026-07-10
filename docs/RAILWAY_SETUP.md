# Railway setup — after connecting GitHub

You connected the repo. Finish these steps in the [Railway dashboard](https://railway.app/dashboard) so the app stays up and keeps user data.

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
| `OPTIONS_PUBLIC_URL` | `https://YOUR-SERVICE.up.railway.app` | Yes |
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

Replace with your Railway URL:

```bash
curl https://YOUR-SERVICE.up.railway.app/health
# → {"status":"ok","users":0}

open https://YOUR-SERVICE.up.railway.app/signup
```

Create an account → open **TV** tab → copy webhook URL for TradingView.

## 6. TradingView webhook

Webhook URL:

```
https://YOUR-SERVICE.up.railway.app/webhook/tradingview
```

Each user’s **personal secret** is on the TV tab after sign-in (embedded in the Pine script). TradingView needs paid plan + 2FA.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Build fails “Dockerfile not found” | Set Dockerfile path to `Dockerfile.web` |
| Health check failing | Wait 30s after start; check deploy logs for Python errors |
| 502 / crash on start | Add LLM API key; check logs for import errors |
| Sign-in works but data lost on redeploy | Volume at `/data` + `OPTIONS_DATA_DIR=/data` |
| TV wizard shows `localhost` URL | Set `OPTIONS_PUBLIC_URL` to Railway HTTPS URL |
| Lovable UI can’t call API | Set `OPTIONS_CORS_ORIGINS` to your Lovable preview URL |

## Local vs Railway

- **Develop locally:** `make dev` (no Railway deploy needed)
- **Ship to Railway:** push to GitHub only when `make deploy-check` passes

## Optional: custom domain

**Settings → Networking → Custom Domain** → point DNS → update `OPTIONS_PUBLIC_URL` to match.
