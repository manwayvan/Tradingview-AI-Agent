# Netlify — point production at `main` only

Netlify site: **[moneymaker9000](https://app.netlify.com/projects/moneymaker9000)**  
Public URL: `https://moneymaker9000.netlify.app`

## Why builds fail

If deploy logs say:

```text
git ref refs/heads/claude/options-trading-agent-k4y8cr does not exist
```

Netlify’s **Production branch** is still set to the deleted Claude branch.
That branch was removed on purpose — this repo is **`main` only**
([BRANCH_POLICY.md](./BRANCH_POLICY.md)). **Do not recreate**
`claude/options-trading-agent-k4y8cr`.

## Fix (one-time, in Netlify UI)

1. Open [Site configuration → Build & deploy → Continuous deployment](https://app.netlify.com/projects/moneymaker9000/configuration/deploys)
2. Under **Branches**, set **Production branch** to **`main`**
3. Save
4. **Deploys → Trigger deploy → Deploy site**

After that, every push to `main` builds from `netlify.toml` (static landing that
redirects to Railway). The trading API does **not** run on Netlify — see
[DEPLOYMENT.md](./DEPLOYMENT.md).

## What this repo publishes to Netlify

| Path | Role |
|------|------|
| `netlify.toml` | Build: publish `netlify/public` only |
| `netlify/public/index.html` | Redirect / link to Railway production |

Railway URL: `https://moneymaker9000.up.railway.app`

## Optional: stop using Netlify

If you do not need the `*.netlify.app` redirect domain:

1. Netlify → Site configuration → **General → Delete site**, or  
2. Disconnect the GitHub repo under **Build & deploy → Continuous deployment**

Use Railway as the only production host ([RAILWAY_SETUP.md](./RAILWAY_SETUP.md)).
