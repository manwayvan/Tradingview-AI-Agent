# Lovable project brief — Options AI Agent frontend (optional)

Use this brief when creating a **new Lovable project** for a React dashboard that
talks to the existing Python API deployed on Railway/Render.

## Product

Mobile-first paper options trading dashboard. Users sign in, connect TradingView,
enable an autonomous AI brain, manage strategies, and view positions/P&L.

## Backend API (already deployed separately)

- Base URL: set `VITE_API_URL` to your Railway/Render URL (e.g. `https://xxx.up.railway.app`)
- Auth: cookie-based (`credentials: 'include'` on all fetch calls)
- CORS: backend must list this Lovable preview URL in `OPTIONS_CORS_ORIGINS`

### Key endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/auth/signup` | Create account |
| POST | `/api/auth/login` | Sign in |
| POST | `/api/auth/logout` | Sign out |
| GET | `/api/auth/me` | Current user |
| GET | `/api/state` | Dashboard poll (account, positions, engine, autonomous) |
| GET | `/api/tradingview/setup` | Webhook URL, secret, Pine scripts, steps |
| POST | `/api/tradingview/connect` | Save TradingView username |
| POST | `/api/autonomous/toggle` | Enable/pause AI brain |
| POST | `/api/autonomous/run` | Run cycle now |
| POST | `/api/strategies` | Add strategy |
| POST | `/positions/{id}/close` | Close position |

## Screens (bottom tab navigation)

1. **Home** — equity tiles, open/closed positions, activity feed
2. **AI** — autonomous brain toggle, run now, risk metrics, events
3. **Plans** — strategy list + add form (ticker, mode, trigger)
4. **TradingView** — setup wizard, copy webhook URL/secret/Pine, username form
5. **Account** — email, TV status, sign out

## Design

- Dark/light system preference
- Touch targets ≥ 44px
- shadcn/ui + Tailwind
- PWA-friendly (manifest, mobile viewport)

## TradingView note (show in UI)

TradingView has no third-party OAuth. Users sign in on tradingview.com, then paste
their webhook URL from this app into TradingView alerts. Paid plan + 2FA required.

## Env vars (Lovable project)

```
VITE_API_URL=https://your-backend.up.railway.app
```

## Do not implement in Lovable

- Options pricing / LLM agent graph (lives in Python backend)
- Webhook receiver (backend only)
- SQLite / file storage
