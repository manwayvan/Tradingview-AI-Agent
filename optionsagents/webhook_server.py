"""Web server: GUI dashboard, strategy engine, and TradingView webhook.

One process does everything:

- ``GET /`` serves the dashboard GUI — add set-and-forget strategies, watch
  positions and P&L, and see the activity feed.
- The :class:`~optionsagents.engine.StrategyEngine` runs in the background,
  executing scheduled strategies and enforcing stop/target/expiry exits.
- ``POST /webhook/tradingview`` accepts TradingView alerts (templates in
  ``pine/``). TradingView requires webhook URLs to be reachable from the
  internet on port 80/443 — for local testing use a tunnel such as
  ``ngrok http 8000``.

Run:  python run_options.py serve
  or: uvicorn optionsagents.webhook_server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

from optionsagents.engine import (
    DEFAULT_STRATEGIES_FILE,
    Strategy,
    StrategyEngine,
)
from optionsagents.paper_broker import PaperBroker
from optionsagents.pipeline import DEFAULT_ACCOUNT_FILE, OptionsPipeline
from optionsagents.schemas import TradingViewAlert

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state: one broker (one account file), one pipeline per mode, one engine
# ---------------------------------------------------------------------------

_pipelines: dict[str, OptionsPipeline] = {}
_broker: PaperBroker | None = None
_engine: StrategyEngine | None = None
_lock = threading.Lock()


def _account_file() -> str:
    return os.environ.get("OPTIONS_ACCOUNT_FILE", DEFAULT_ACCOUNT_FILE)


def get_broker() -> PaperBroker:
    global _broker
    with _lock:
        if _broker is None:
            _broker = PaperBroker(_account_file())
        return _broker


def get_pipeline(mode: str) -> OptionsPipeline:
    broker = get_broker()
    with _lock:
        if mode not in _pipelines:
            _pipelines[mode] = OptionsPipeline(mode=mode, broker=broker)
        return _pipelines[mode]


def _run_strategy(strat: Strategy) -> str:
    """Engine callback: execute one strategy and return a short outcome."""
    pipeline = get_pipeline(strat.mode)
    if strat.signal in ("buy", "sell"):
        result = pipeline.run_signal(
            strat.ticker, strat.signal,
            context=f"Scheduled {strat.signal.upper()} strategy ({strat.describe_schedule()})",
        )
    else:
        result = pipeline.run(strat.ticker)
    if result.position:
        return (
            f"opened {result.plan.strategy.value} "
            f"(id {result.position.id}, max risk ${result.position.max_risk:,.0f})"
        )
    reason = result.warnings[0] if result.warnings else result.plan.rationale
    return f"no trade — {reason}" if reason else "no trade"


def _check_positions() -> list:
    """Engine callback: mark all open positions and apply exits."""
    return get_pipeline("day").check_positions()


def get_engine() -> StrategyEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = StrategyEngine(
                run_strategy=_run_strategy,
                check_positions=_check_positions,
                strategies_file=os.environ.get(
                    "OPTIONS_STRATEGIES_FILE", DEFAULT_STRATEGIES_FILE
                ),
            )
        return _engine


@asynccontextmanager
async def _lifespan(app: FastAPI):
    get_engine().start()
    yield
    get_engine().stop()


app = FastAPI(
    title="TradingView Options Agent",
    description="LLM multi-agent options paper trading with a set-and-forget "
                "strategy engine and TradingView alert webhooks.",
    lifespan=_lifespan,
)

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    with open(os.path.join(_STATIC_DIR, "index.html")) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------


@app.get("/api/state")
def state() -> dict:
    """Everything the dashboard needs, in one poll."""
    broker = get_broker()
    return {
        "account": broker.summary(),
        "positions": [asdict(p) for p in broker.positions()],
        "journal": broker._state["journal"][-50:][::-1],
        "engine": get_engine().snapshot(),
        "webhook_secret_set": bool(os.environ.get("TRADINGVIEW_WEBHOOK_SECRET")),
    }


class StrategyRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    mode: str = Field(pattern="^(day|swing)$")
    trigger: str = Field(pattern="^(daily|interval|webhook)$")
    signal: str = Field(default="analyze", pattern="^(analyze|buy|sell)$")
    run_time: str = Field(default="10:00", pattern=r"^\d{1,2}:\d{2}$")
    interval_minutes: int = Field(default=60, ge=5, le=390)


@app.post("/api/strategies")
def add_strategy(req: StrategyRequest) -> dict:
    strat = get_engine().add(
        ticker=req.ticker.strip().upper(),
        mode=req.mode,
        trigger=req.trigger,
        signal=req.signal,
        run_time=req.run_time,
        interval_minutes=req.interval_minutes,
    )
    return asdict(strat)


@app.delete("/api/strategies/{strategy_id}")
def delete_strategy(strategy_id: str) -> dict:
    if not get_engine().remove(strategy_id):
        raise HTTPException(status_code=404, detail="unknown strategy")
    return {"deleted": strategy_id}


@app.post("/api/strategies/{strategy_id}/toggle")
def toggle_strategy(strategy_id: str) -> dict:
    engine = get_engine()
    strat = engine.get(strategy_id)
    if strat is None:
        raise HTTPException(status_code=404, detail="unknown strategy")
    engine.set_enabled(strategy_id, not strat.enabled)
    return {"id": strategy_id, "enabled": strat.enabled}


@app.post("/api/strategies/{strategy_id}/run")
def run_strategy_now(strategy_id: str) -> dict:
    if not get_engine().run_now(strategy_id):
        raise HTTPException(status_code=409, detail="strategy missing or already running")
    return {"started": strategy_id}


# ---------------------------------------------------------------------------
# Positions API (also used by the GUI buttons)
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engine_running": get_engine().running}


@app.get("/account")
def account() -> dict:
    return get_broker().summary()


@app.get("/positions")
def positions(status: str | None = None) -> list[dict]:
    return [asdict(p) for p in get_broker().positions(status)]


@app.get("/journal")
def journal(limit: int = 50) -> list[dict]:
    return get_broker()._state["journal"][-limit:]


@app.post("/positions/check")
def check_positions() -> dict:
    closed = _check_positions()
    return {
        "closed": [
            {"id": p.id, "underlying": p.underlying, "pnl": p.realized_pnl,
             "reason": p.exit_reason}
            for p in closed
        ]
    }


@app.post("/positions/{position_id}/close")
def close_position(position_id: str) -> dict:
    pipeline = get_pipeline("day")
    pos = get_broker().get_position(position_id)
    if pos is None or pos.status != "open":
        raise HTTPException(status_code=404, detail=f"no open position {position_id}")
    snapshot = pipeline._snapshot_for_positions(pos.underlying, [pos])
    net = get_broker().mark_position(pos, snapshot)
    if net is None:
        raise HTTPException(status_code=409, detail="no quotes available to price the close")
    closed = get_broker().close_position(pos.id, net, reason="manual")
    return {"id": closed.id, "pnl": closed.realized_pnl, "exit_net": closed.exit_net}


# ---------------------------------------------------------------------------
# TradingView webhook
# ---------------------------------------------------------------------------


def _check_secret(supplied: str) -> None:
    expected = os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="TRADINGVIEW_WEBHOOK_SECRET is not set on the server",
        )
    if not hmac.compare_digest(supplied or "", expected):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def _run_alert(alert: TradingViewAlert) -> None:
    """Background task: alerts return 200 immediately; work happens here."""
    try:
        pipeline = get_pipeline(alert.mode)
        if alert.signal in ("buy", "sell"):
            context = (
                f"TradingView alert: {alert.signal.upper()} {alert.ticker}"
                + (f" at {alert.price}" if alert.price else "")
                + (f" on the {alert.interval} chart" if alert.interval else "")
                + (f". Note: {alert.note}" if alert.note else "")
            )
            result = pipeline.run_signal(alert.ticker, alert.signal, context)
        else:
            result = pipeline.run(alert.ticker)
        logger.info(
            "alert processed: %s %s -> %s (position=%s)",
            alert.ticker, alert.signal, result.plan.strategy.value,
            result.position.id if result.position else None,
        )
    except Exception:
        logger.exception("alert processing failed for %s", alert.ticker)


@app.post("/webhook/tradingview")
async def tradingview_webhook(payload: dict, background: BackgroundTasks) -> dict:
    try:
        alert = TradingViewAlert.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    _check_secret(alert.secret)
    background.add_task(_run_alert, alert)
    return {
        "accepted": True,
        "ticker": alert.ticker,
        "signal": alert.signal,
        "mode": alert.mode,
        "detail": "processing in background; check /positions for the result",
    }


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("OPTIONS_WEBHOOK_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
