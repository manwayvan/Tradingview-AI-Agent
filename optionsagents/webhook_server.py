"""TradingView webhook server.

Point a TradingView alert's webhook URL at ``POST /webhook/tradingview``
with a JSON message (templates in ``pine/``). The server authenticates the
shared secret, then runs the options pipeline: the alert's ``signal``
drives the fast path (buy/sell) or the full multi-agent research path
(analyze). Fills land in the local paper account.

TradingView requires webhook URLs to be reachable from the internet on
port 80/443 — for local testing put the server behind a tunnel such as
``ngrok http 8000``.

Run:  uvicorn optionsagents.webhook_server:app --host 0.0.0.0 --port 8000
  or: python -m optionsagents.webhook_server
"""

from __future__ import annotations

import hmac
import logging
import os
import threading

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import ValidationError

from optionsagents.pipeline import DEFAULT_ACCOUNT_FILE, OptionsPipeline
from optionsagents.schemas import TradingViewAlert

logger = logging.getLogger(__name__)

app = FastAPI(
    title="TradingView Options Agent",
    description="Receives TradingView alerts and paper-trades options via LLM agents.",
)

# One pipeline per mode so day and swing keep their own risk parameters
# while sharing a single paper account file.
_pipelines: dict[str, OptionsPipeline] = {}
_lock = threading.Lock()


def _account_file() -> str:
    return os.environ.get("OPTIONS_ACCOUNT_FILE", DEFAULT_ACCOUNT_FILE)


def get_pipeline(mode: str) -> OptionsPipeline:
    with _lock:
        if mode not in _pipelines:
            _pipelines[mode] = OptionsPipeline(mode=mode, account_file=_account_file())
        return _pipelines[mode]


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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


@app.get("/account")
def account() -> dict:
    broker = get_pipeline("day").broker
    return broker.summary()


@app.get("/positions")
def positions(status: str | None = None) -> list[dict]:
    from dataclasses import asdict

    broker = get_pipeline("day").broker
    return [asdict(p) for p in broker.positions(status)]


@app.get("/journal")
def journal(limit: int = 50) -> list[dict]:
    broker = get_pipeline("day").broker
    return broker._state["journal"][-limit:]


@app.post("/positions/check")
def check_positions() -> dict:
    """Mark open positions to market and apply stop/target/expiry exits."""
    closed = get_pipeline("day").check_positions()
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
    pos = pipeline.broker.get_position(position_id)
    if pos is None or pos.status != "open":
        raise HTTPException(status_code=404, detail=f"no open position {position_id}")
    snapshot = pipeline._snapshot_for_positions(pos.underlying, [pos])
    net = pipeline.broker.mark_position(pos, snapshot)
    if net is None:
        raise HTTPException(status_code=409, detail="no quotes available to price the close")
    closed = pipeline.broker.close_position(pos.id, net, reason="manual")
    return {"id": closed.id, "pnl": closed.realized_pnl, "exit_net": closed.exit_net}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("OPTIONS_WEBHOOK_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
