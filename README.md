# TradingView AI Agent — Multi-Agent LLM Options Trading

An AI options-trading system for **day trading** and **swing trading**, built on top of
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache-2.0). The upstream framework's analyst/researcher/risk agent teams
produce a directional view on the underlying; this repo adds an **options
layer** that turns that view into concrete, defined-risk option positions and
paper-trades them — driven from a **web GUI with a set-and-forget strategy
engine**, the CLI, or **TradingView alerts** via webhook.

> ⚠️ **Nothing here is financial advice.** Options can lose 100% of the
> premium (and defined-risk spreads their full width) very quickly,
> especially at short DTE, and LLM output is non-deterministic and can be
> wrong. Every account starts on **paper trading** and stays there until
> you explicitly connect a real Schwab account and switch to live — see
> [Live trading (Schwab)](#live-trading-schwab). Test extensively on paper
> first regardless.

## What's in the box

```
tradingagents/    Upstream multi-agent research framework (vendored, Apache-2.0)
cli/              Upstream interactive CLI (tradingagents command)
optionsagents/    NEW: the options trading layer
  modes.py          Day vs swing presets (DTE window, delta band, risk, exits)
  chain.py          Options chain snapshots, IV/expected-move metrics, liquidity filters
  greeks.py         Black-Scholes pricing, greeks, implied vol (stdlib only)
  strategist.py     LLM Options Strategist agent (structured output + fallback)
  paper_broker.py   Local paper account: mid fills, slippage, stops/targets, JSON ledger
  pipeline.py       Research -> chain -> strategist -> risk gate -> paper fill
  engine.py         Set-and-forget strategy scheduler + automatic exit management
  autonomous/       NEW: self-directed AI brain (scan → decide → execute)
  brokers/          NEW: live Schwab execution (OAuth, order placement, fill-or-cancel)
  webhook_server.py Web server: GUI dashboard, strategy API, TradingView webhook
  static/index.html The dashboard GUI (self-contained, light/dark)
  cli.py            optionsagents / run_options.py command line
pine/             TradingView Pine Script alert templates (day + swing)
scripts/schwab_login.py  One-time interactive Schwab OAuth login
tests/            Offline unit tests for the options layer
docs/UPSTREAM_README.md   Original TradingAgents README
```

## How a trade happens

```
TradingView alert or CLI
        │
        ▼
┌─ Full research path (swing) ─────────────────────────────┐
│ Market / News / Sentiment / Fundamentals analysts        │
│   -> Bull vs Bear researcher debate -> Research Manager  │
│   -> Trader -> Risk team -> Portfolio Manager rating     │
└──────────────────────────────────────────────────────────┘
        │  Buy / Overweight -> bullish · Sell / Underweight -> bearish · Hold -> stand aside
        ▼
Options chain snapshot (yfinance): spot, ATM IV, expected move,
put/call ratios, liquid strikes inside the mode's delta band
        ▼
Options Strategist (LLM) picks ONE defined-risk structure:
long call/put, or a vertical debit/credit spread — or no_trade
        ▼
Risk gate: max loss per position enforced, contracts clamped,
illiquid chains rejected, max open positions respected
        ▼
Paper broker: fills at mid ± slippage, tracks P&L, applies
profit-target / stop-loss / expiry exits, journals every event
```

The **day-trading fast path** (`signal buy|sell`) skips the multi-agent
debate — a scalp setup is gone in minutes — and goes straight from the
TradingView signal to the strategist. The **swing path** (`analyze`) runs the
full research pipeline first and only trades if the researched direction
agrees with a tradable setup.

| Mode | DTE window | \|Delta\| band | Max risk/trade | Target / Stop | Liquidity gates |
|---|---|---|---|---|---|
| `day` | 0–5 | 0.45–0.70 | $500 | +50% / −30% of risk | OI ≥ 500, spread ≤ 10% |
| `swing` | 14–60 | 0.30–0.60 | $1,000 | +100% / −50% of risk | OI ≥ 100, spread ≤ 15% |

Tune these in `optionsagents/modes.py` or via `get_mode("day", max_risk_per_trade=250)`.

## Setup

```bash
git clone https://github.com/manwayvan/Tradingview-AI-Agent.git
cd Tradingview-AI-Agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Edit .env: set at least one LLM key (OPENAI_API_KEY / ANTHROPIC_API_KEY /
# GOOGLE_API_KEY, ...) and TRADINGVIEW_WEBHOOK_SECRET
```

Market and options-chain data come from yfinance by default (no key needed).

## Branch & deploy

**Single branch:** `main` only — no forks, no feature branches. Railway deploys from `main`.  
See [docs/BRANCH_POLICY.md](docs/BRANCH_POLICY.md). Production URL:
`https://tradingview-ai-agent-production.up.railway.app`  
If Netlify fails looking for `claude/options-trading-agent-k4y8cr`, point its
production branch at `main` — [docs/NETLIFY.md](docs/NETLIFY.md).

## Local development (test before deploy)

**You do not need Netlify (or any host) to use this app.** Run everything on your machine:

```bash
make install    # once
make dev        # web app + hot reload at http://localhost:8000
make test       # full test suite before you commit
make tunnel     # HTTPS URL for TradingView webhooks (no deploy)
```

See **[docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md)** for the full workflow,
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for Railway/Render/Fly, and
**[docs/RAILWAY_SETUP.md](docs/RAILWAY_SETUP.md)** if you connected GitHub to Railway.

## Quick start: the web app (PC + mobile)

```bash
python run_options.py serve
```

Then open **http://localhost:8000** — you'll land on the sign-in page.

1. **Create an account** (email + password). Each user gets an isolated paper ledger, strategies, and AI brain — **the scanner turns itself on at signup**, no setup step required.
2. That's it. Every 5 minutes during market hours the server discovers candidates across the whole market from free Yahoo screeners (most actives, top gainers/losers, trending), filters out illiquid names, applies the same EMA/VWAP/RSI rules as the Pine scripts, **and** lets the AI brain rank and pick the best setups — opening paper trades automatically. No watchlist, no TradingView subscription, nothing to click. Check the **Scanner** tab any time to see it running or pause it.
3. *(Optional)* Open the **TV** tab if you have a **paid** TradingView plan and want chart alerts via webhook instead.
4. *(Optional)* Add scheduled per-ticker strategies from **Plans**.

**Zero-touch by design:** the FastAPI server resumes every account's scanner threads automatically on boot (its `lifespan` startup hook). As long as the process is running and its data directory persists (see below), you never have to re-enable anything after a redeploy or restart.

**Mobile:** open the site in Safari/Chrome and use **Add to Home Screen** for a full-screen app experience. The bottom navigation works on phone and desktop.

**Production deploy:** host on any HTTPS domain (TradingView requires public port 443). Set:

```bash
export OPTIONS_PUBLIC_URL=https://your-domain.com
export OPTIONS_COOKIE_SECURE=true
```

## Quick start: the GUI (legacy single-user)

The same `serve` command now uses account sign-in by default. For CLI-only single-user mode without the web database, use the `analyze` / `signal` commands directly — they still write to `~/.tradingagents/paper_account.json`.

- **Set and forget.** Add a strategy — ticker, action (`analyze` = full
  multi-agent research, or a fixed `buy`/`sell` bias), day or swing mode, and
  a schedule (daily at a time, every N minutes during market hours, or
  webhook-only). The background engine runs it automatically from then on.
- **Automatic exits.** While the market is open the engine re-marks every
  open position every 5 minutes and closes anything that hits its profit
  target, stop loss, or expiry day — no babysitting required.
- **Live account view.** Equity, cash, unrealized/realized P&L, win rate,
  open and closed positions (with a one-click Close), and an activity feed
  of everything the engine did while you were away.
- **Survives restarts.** Strategies persist to
  `~/.tradingagents/strategies.json` and the account to
  `~/.tradingagents/paper_account.json`; restarting the server picks both up.

A typical set-and-forget setup: add `analyze NVDA, swing, daily at 10:00`,
plus a couple of TradingView day-signal alerts (below), then just leave the
server running and check the dashboard in the evening.

## Autonomous AI brain (self-sufficient operation)

The **Autonomous AI brain** is a fully self-directed layer on top of the
existing pipeline. You do not pick tickers or strategies manually — the system
does it for you:

1. **Market scanner** — ranks a liquid-options universe (30 names by default)
   using momentum, relative strength vs SPY, RSI, volume surge, trend, and
   volatility.
2. **Market context** — reads SPY trend and VIX to classify the regime
   (risk-on, risk-off, volatile, neutral).
3. **Strategy brain (LLM CIO)** — receives ranked candidates, regime, open
   positions, and past decision memory; picks 0–2 high-conviction trades with
   mode (day/swing) and signal (analyze/buy/sell). Falls back to deterministic
   rules when no LLM key is set.
4. **Portfolio risk manager** — enforces daily loss limits, total open risk
   caps, and per-ticker concentration before any trade fires.
5. **Orchestrator** — runs the full cycle on a schedule during market hours,
   executes through the options pipeline, and journals everything.

```bash
# Start server with autonomous brain enabled
export AUTONOMOUS_ENABLED=true
python run_options.py serve

# Or enable from the dashboard ("Autonomous AI brain" card)

# CLI: scan universe once, run one cycle, or check status
python run_options.py autonomous scan --top 10
python run_options.py autonomous run
python run_options.py autonomous status
python run_options.py autonomous enable
```

Tune via environment variables (see `.env.example`): universe, cycle interval,
max trades per cycle, conviction threshold, daily loss kill switch, and open
risk cap.

### Web app pages

| URL | Purpose |
|-----|---------|
| `/signup` | Create account |
| `/login` | Sign in |
| `/app` | Mobile-friendly dashboard (Home, Orders, Stats, Scanner, Plans, TV, Account) |

## Live trading (Schwab)

> ⚠️ **This places real orders with real money.** Everything above this
> section is paper trading. Live trading is off by default for every
> account and stays off until you explicitly connect a Schwab account and
> switch a specific account to Live — there is no automatic upgrade path
> from paper to live.

The app can place real options orders through the **Schwab Trader API**,
the official successor to TD Ameritrade's API (TDA's own API was retired
after the Schwab migration — if you have an old TDA account, it's a Schwab
account now and this is the right integration). Robinhood and Webull were
considered and deliberately not implemented: Robinhood has no official
trading API (only reverse-engineered private endpoints that can break
without notice and have led to account suspensions for automated options
trading), and Webull's official API's options support is newer and less
established. Schwab is the only one of the three with a documented,
sanctioned path for automated options orders.

**How it fits together:** the scanner and AI brain don't know or care
which broker they're trading through — they build the same trade plan
either way. A separate `account_mode` per user (`paper` or `live`)
decides whether that plan gets filled locally (`PaperBroker`) or actually
submitted to Schwab (`SchwabBroker`, in `optionsagents/brokers/`). Paper
and live are entirely separate ledgers, order histories, and P&L —
switching modes never touches the other one.

### One-time setup

1. Register a developer app at [developer.schwab.com](https://developer.schwab.com)
   and request Trader API access (individual/retail trading, not just
   market data). Schwab's approval can take a few days.
2. Set the app's redirect URI to `https://127.0.0.1` (you'll copy/paste the
   redirected URL by hand — no local callback server needed).
3. Set these on both your local shell (for the login script) **and** your
   production server's environment variables (Railway service variables,
   etc. — the running app needs them to refresh tokens and place orders):
   ```bash
   SCHWAB_APP_KEY=...
   SCHWAB_APP_SECRET=...
   SCHWAB_REDIRECT_URI=https://127.0.0.1
   ```
4. Run the one-time interactive login (this step requires a real browser
   login to schwab.com — it can't be automated):
   ```bash
   python scripts/schwab_login.py --email you@example.com
   ```
   This saves OAuth tokens to that account's data directory. Schwab's
   refresh token expires after roughly a week of inactivity — when the
   Account tab shows "Not connected," rerun this script.
5. In the app's **Account** tab, under **Live trading**, tap **Switch to
   Live**. It's disabled until Schwab shows Connected.

### What changes once you're live

- **Automatic execution, no approval step** — the scanner and AI brain
  place real orders the instant they decide, on the same 5-minute cadence
  as paper trading. There is currently no "approve before it fires" mode.
- **Independent risk settings** — the Account tab's **Live trading** card
  has its own risk-per-trade and max-portfolio-risk percentages, separate
  from the paper account's. They default small (1% per trade, 10%
  portfolio) specifically so a bug or a bad week costs little while you
  validate the integration; raise them once you trust it.
- **Fill-or-cancel, never a dangling order** — every live order is
  submitted as a limit at the net mid and polled for ~12 seconds; if it
  hasn't filled, it's canceled and recorded as a skipped trade, the same
  way the paper broker records "insufficient cash." The system never
  leaves a live working order it's lost track of.
- **Schwab is the source of truth for cash/equity** — the Account tab and
  API pull your real balance from Schwab on every request; the local
  ledger exists for the "why did this trade happen" audit trail, same as
  paper.
- **No reset button** — obviously; there's no such thing as resetting a
  real brokerage account. That control only ever applies to paper.

### Before you trust the automatic loop

This is a straightforward, load-bearing integration but it has not been
exercised against Schwab's live API from this environment (no
credentials, no network path to verify against) — the order-payload
shapes and endpoint paths were written from Schwab's documented
conventions, not confirmed against a live account. **Before letting the
5-minute loop run unsupervised:**

1. Confirm Schwab shows Connected and the live balance tiles match your
   actual account.
2. Trigger one manual cycle (Scanner tab → **Scan now**, or wait for a
   signal) with your live risk settings still at the small defaults, and
   watch it happen in the Schwab app in real time.
3. Check the order in **Orders** → tap it → confirm the fill price, legs,
   and strategy match what actually executed in Schwab.
4. Only then raise the live risk percentages, and only as far as you're
   genuinely comfortable losing on a bad week — this system already had
   one production bug (a plan-validation crash, fixed) and can have
   others; automated options trading can lose 100% of a position's
   premium quickly.

## CLI usage

```bash
# Swing trade: full multi-agent research, then an options position on paper
python run_options.py analyze NVDA --mode swing

# Day trade fast path: act on a directional signal immediately
python run_options.py signal SPY buy --mode day

# No LLM at all (deterministic strike selection; good for a dry run)
python run_options.py signal SPY buy --mode day --no-llm

# Account maintenance
python run_options.py account            # cash, equity, realized/unrealized P&L
python run_options.py positions          # every position with fills and exits
python run_options.py mark               # mark-to-market + auto stop/target/expiry exits
python run_options.py close <positionId> # close at current mid

# The upstream equity research CLI still works as-is
tradingagents
```

When the GUI server is running, exits are enforced automatically. If you only
use the CLI, run `python run_options.py mark` periodically while positions
are open — that's what enforces stops, targets, and expiry closes.

## Free built-in signals (no TradingView)

The app includes a **free signal engine** that replicates the Pine script logic on the server:

| Mode | Rules | Action |
|------|-------|--------|
| Day (5m) | EMA 9/21 crossover + VWAP filter | `buy` / `sell` → fast options path |
| Swing (daily) | EMA 20/50 crossover + RSI band | `analyze` → full multi-agent research |

- Enabled by default for every account; **the scan universe is discovered
  automatically** from free Yahoo screeners (most actives, day gainers/losers,
  trending) filtered for price, dollar volume, and market cap — no watchlist
  to maintain. Falls back to a built-in liquid universe if the screeners are
  unreachable.
- Scans during US market hours (day) and once daily after 10:05 ET (swing).
- Uses yfinance for price data — no paid charting subscription.

TradingView webhooks remain available as an **optional** upgrade for users who already pay for TradingView Pro.

## TradingView integration (optional, paid plan)

TradingView does not expose a public order-execution API — its built-in Paper
Trading account can't be driven programmatically. So the integration works in
the direction TradingView *does* support, **outbound alerts → your webhook**,
and execution is simulated in the local paper broker, which mimics a paper
account (mid-price fills with slippage, full ledger). The trade journal gives
you everything needed to mirror positions in TradingView's own Paper Trading
panel if you want a side-by-side comparison.

1. **Run the webhook server** (TradingView requires a public HTTPS URL, port 80/443):
   ```bash
   export TRADINGVIEW_WEBHOOK_SECRET="some-long-random-string"
   python run_options.py serve --port 8000
   # in another terminal, for local testing:
   ngrok http 8000
   ```
2. **Add a signal script in TradingView**: open the Pine Editor, paste
   `pine/day_trade_signal.pine` (5-minute chart) or
   `pine/swing_trade_signal.pine` (daily chart), and replace
   `REPLACE_WITH_YOUR_SECRET` with your secret.
3. **Create one alert** on the indicator with condition **"Any alert()
   function call"**, and set the webhook URL to
   `https://<your-tunnel>/webhook/tradingview`. The scripts send the JSON
   payload themselves; leave the message box alone.
4. Alerts now flow: day signals open paper trades within seconds; swing
   signals kick off the full research pipeline first (takes a few minutes).

Server endpoints: `GET /` (the GUI), `GET /api/state`,
`POST /api/strategies` (+ `/toggle`, `/run`, `DELETE`), `GET /health`,
`GET /account`, `GET /positions`, `GET /journal`,
`POST /positions/check` (mark + exits), `POST /positions/{id}/close`,
`POST /webhook/tradingview`.

> The GUI and its API are unauthenticated by design (local tool). If you
> tunnel the port for TradingView, only the `/webhook/tradingview` path is
> secret-protected — prefer a tunnel that lets you restrict paths, or keep
> the tunnel URL private.

Alert payload shape (what the Pine templates send):

```json
{"secret": "...", "ticker": "NVDA", "signal": "buy", "mode": "day",
 "price": 512.30, "interval": "5", "note": "EMA9/21 cross with VWAP filter"}
```

`signal` may be `buy`, `sell`, or `analyze` (full research). Exchange
prefixes like `NASDAQ:NVDA` are stripped automatically.

## Safety rails

- **Defined-risk only** — long options and vertical spreads; naked short
  options are not even representable in the plan schema.
- **Hard risk cap per position** — the risk gate clamps contract count to the
  mode's max loss, and rejects trades where a single contract exceeds it.
- **Liquidity filters** — strikes must clear open-interest and bid-ask-spread
  gates before the strategist ever sees them; illiquid chains become `no_trade`.
- **Validated plans** — every LLM-proposed leg is checked against the real
  chain (strike/expiry must exist with a usable quote); invalid plans fall
  back to deterministic selection or no trade.
- **Automatic exits** — profit target, stop loss, and expiry-day closes are
  enforced by `mark` / `POST /positions/check`.
- **Authenticated webhook** — requests must carry the shared secret
  (constant-time compared); the server refuses to start trading without one.
- **Live trading opt-in only** — every account starts and stays on paper;
  switching to live requires a connected Schwab account and an explicit,
  confirmed action in the Account tab. See "Live trading (Schwab)" above.
- **Live risk isolated from paper** — separate risk-per-trade and
  max-portfolio-risk settings for live vs. paper, so raising paper-trading
  risk to test something never silently raises real-money risk.
- **Fill-or-cancel on live orders** — a live order that doesn't fill within
  ~12 seconds is canceled, never left as an untracked working order.

## Tests

```bash
pytest tests/test_options_greeks.py tests/test_options_chain.py \
       tests/test_paper_broker.py tests/test_options_schemas.py \
       tests/test_options_pipeline.py tests/test_strategy_engine.py -q
```

All options-layer tests are offline (synthetic chains, no LLM, no network).

## Attribution

The `tradingagents/`, `cli/`, and original `tests/` code is from
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents),
used under the Apache License 2.0 (see `LICENSE`); their original README is
preserved at `docs/UPSTREAM_README.md`. If you use the research framework in
academic work, cite their paper: [TradingAgents: Multi-Agents LLM Financial
Trading Framework](https://arxiv.org/abs/2412.20138).
