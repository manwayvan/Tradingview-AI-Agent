"""Authenticated API routes and auth endpoints."""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError

from optionsagents.schemas import TradingViewAlert
from optionsagents.webapp.auth import (
    User,
    authenticate,
    clear_session_cookie,
    create_session,
    create_user,
    delete_session,
    get_user_by_webhook_secret,
    regenerate_webhook_secret,
    require_user,
    set_session_cookie,
    update_account_settings,
    update_tradingview,
)
from optionsagents.webapp.tradingview import pine_script_for_user, tradingview_setup_steps
from optionsagents.webapp.workspaces import UserWorkspace, get_workspace_manager

logger = logging.getLogger(__name__)

router = APIRouter()


def _base_url(request: Request) -> str:
    configured = os.environ.get("OPTIONS_PUBLIC_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def _ws(user: User, request: Request) -> UserWorkspace:
    return get_workspace_manager().get(user)


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class TradingViewConnectRequest(BaseModel):
    tradingview_username: str = Field(min_length=1, max_length=64)
    confirm: bool = False


class WatchlistRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=50)


class AccountSettingsRequest(BaseModel):
    starting_cash: float | None = Field(default=None, ge=1000, le=10_000_000)
    risk_pct_per_trade: float | None = Field(default=None, ge=0.5, le=50)
    max_portfolio_risk_pct: float | None = Field(default=None, ge=5, le=100)
    reset_paper: bool = False
    clear_history: bool = True


class BrokerModeRequest(BaseModel):
    account_mode: str = Field(pattern="^(paper|live)$")


class LiveRiskSettingsRequest(BaseModel):
    risk_pct_per_trade: float | None = Field(default=None, ge=0.1, le=25)
    max_portfolio_risk_pct: float | None = Field(default=None, ge=1, le=75)


class StrategyRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    mode: str = Field(pattern="^(day|swing)$")
    trigger: str = Field(pattern="^(daily|interval|webhook)$")
    signal: str = Field(default="analyze", pattern="^(analyze|buy|sell)$")
    run_time: str = Field(default="10:00", pattern=r"^\d{1,2}:\d{2}$")
    interval_minutes: int = Field(default=60, ge=5, le=390)


@router.get("/api/auth/me")
def auth_me(request: Request, user: User = Depends(require_user)) -> dict:
    ws = _ws(user, request)
    return {
        "user": user.to_public_dict(_base_url(request)),
        "has_open_positions": bool(ws.broker.positions("open")),
    }


@router.post("/api/auth/signup")
def auth_signup(req: SignupRequest, response: Response, request: Request) -> dict:
    try:
        user = create_user(req.email, req.password, req.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = create_session(user.id)
    set_session_cookie(response, token, request)
    get_workspace_manager().get(user)
    return {"user": user.to_public_dict(_base_url(request)), "ok": True}


@router.post("/api/auth/login")
def auth_login(req: LoginRequest, response: Response, request: Request) -> dict:
    user = authenticate(req.email, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    token = create_session(user.id)
    set_session_cookie(response, token, request)
    get_workspace_manager().get(user)
    return {"user": user.to_public_dict(_base_url(request)), "ok": True}


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response, user: User = Depends(require_user)) -> dict:
    from optionsagents.webapp.auth import SESSION_COOKIE

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(token)
    clear_session_cookie(response, request)
    return {"ok": True}


@router.get("/api/state")
def state(request: Request, user: User = Depends(require_user)) -> dict:
    ws = _ws(user, request)
    snap = ws.snapshot_state()
    return {
        **snap,
        "user": user.to_public_dict(_base_url(request)),
        "webhook_secret_set": bool(user.webhook_secret),
    }


@router.get("/api/tradingview/setup")
def tv_setup(request: Request, user: User = Depends(require_user)) -> dict:
    base = _base_url(request)
    webhook_url = f"{base}/webhook/tradingview"
    return {
        "user": user.to_public_dict(base),
        "webhook_secret": user.webhook_secret,
        "steps": tradingview_setup_steps(webhook_url),
        "pine_day": pine_script_for_user(user.webhook_secret, "day"),
        "pine_swing": pine_script_for_user(user.webhook_secret, "swing"),
    }


@router.post("/api/tradingview/connect")
def tv_connect(
    req: TradingViewConnectRequest,
    request: Request,
    user: User = Depends(require_user),
) -> dict:
    updated = update_tradingview(
        user.id, req.tradingview_username, mark_connected=req.confirm,
    )
    get_workspace_manager().get(updated)
    return {"user": updated.to_public_dict(_base_url(request))}


@router.post("/api/tradingview/regenerate-secret")
def tv_regenerate(request: Request, user: User = Depends(require_user)) -> dict:
    updated = regenerate_webhook_secret(user.id)
    get_workspace_manager().get(updated)
    return {
        "user": updated.to_public_dict(_base_url(request)),
        "pine_day": pine_script_for_user(updated.webhook_secret, "day"),
        "pine_swing": pine_script_for_user(updated.webhook_secret, "swing"),
    }


@router.post("/api/strategies")
def add_strategy(req: StrategyRequest, request: Request, user: User = Depends(require_user)) -> dict:
    strat = _ws(user, request).engine.add(
        ticker=req.ticker.strip().upper(),
        mode=req.mode,
        trigger=req.trigger,
        signal=req.signal,
        run_time=req.run_time,
        interval_minutes=req.interval_minutes,
    )
    return asdict(strat)


@router.delete("/api/strategies/{strategy_id}")
def delete_strategy(strategy_id: str, request: Request, user: User = Depends(require_user)) -> dict:
    if not _ws(user, request).engine.remove(strategy_id):
        raise HTTPException(status_code=404, detail="unknown strategy")
    return {"deleted": strategy_id}


@router.post("/api/strategies/{strategy_id}/toggle")
def toggle_strategy(strategy_id: str, request: Request, user: User = Depends(require_user)) -> dict:
    engine = _ws(user, request).engine
    strat = engine.get(strategy_id)
    if strat is None:
        raise HTTPException(status_code=404, detail="unknown strategy")
    engine.set_enabled(strategy_id, not strat.enabled)
    return {"id": strategy_id, "enabled": strat.enabled}


@router.post("/api/strategies/{strategy_id}/run")
def run_strategy_now(strategy_id: str, request: Request, user: User = Depends(require_user)) -> dict:
    if not _ws(user, request).engine.run_now(strategy_id):
        raise HTTPException(status_code=409, detail="strategy missing or already running")
    return {"started": strategy_id}


@router.get("/api/autonomous")
def autonomous_state(request: Request, user: User = Depends(require_user)) -> dict:
    ws = _ws(user, request)
    return ws.orchestrator.snapshot(broker=ws.broker)


@router.post("/api/autonomous/toggle")
def toggle_autonomous(request: Request, user: User = Depends(require_user)) -> dict:
    ws = _ws(user, request)
    ws.set_autonomous(not ws.user.autonomous_enabled)
    return {"enabled": ws.user.autonomous_enabled}


@router.post("/api/autonomous/run")
def run_autonomous_now(request: Request, user: User = Depends(require_user)) -> dict:
    if not _ws(user, request).orchestrator.run_now():
        raise HTTPException(status_code=409, detail="cycle already running")
    return {"started": True}


@router.get("/api/orders")
def list_orders(request: Request, user: User = Depends(require_user), limit: int = 100) -> dict:
    orders = _ws(user, request).broker.list_orders(limit)
    return {"orders": [o.to_dict() for o in orders]}


@router.get("/api/orders/{order_id}")
def get_order(order_id: str, request: Request, user: User = Depends(require_user)) -> dict:
    order = _ws(user, request).broker.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return {"order": order.to_dict()}


@router.get("/api/stats")
def performance_stats(request: Request, user: User = Depends(require_user)) -> dict:
    return {"stats": _ws(user, request).performance_stats()}


@router.get("/api/account/settings")
def account_settings(request: Request, user: User = Depends(require_user)) -> dict:
    ws = _ws(user, request)
    snap = ws.snapshot_state()
    return {
        "user": user.to_public_dict(_base_url(request)),
        "account": snap["account"],
        "risk": snap["risk"],
    }


@router.patch("/api/account/settings")
def update_account(
    req: AccountSettingsRequest,
    request: Request,
    user: User = Depends(require_user),
) -> dict:
    try:
        updated = update_account_settings(
            user.id,
            starting_cash=req.starting_cash,
            risk_pct_per_trade=req.risk_pct_per_trade,
            max_portfolio_risk_pct=req.max_portfolio_risk_pct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ws = _ws(user, request)
    if req.reset_paper:
        ws.update_account(
            updated,
            reset_paper=True,
            clear_history=req.clear_history,
        )
    else:
        ws.refresh_user(updated)

    snap = ws.snapshot_state()
    return {
        "user": updated.to_public_dict(_base_url(request)),
        "account": snap["account"],
        "risk": snap["risk"],
    }


@router.post("/api/account/reset")
def reset_account(
    request: Request,
    user: User = Depends(require_user),
    clear_history: bool = True,
) -> dict:
    ws = _ws(user, request)
    # Always the paper ledger — there is no "reset" on a real brokerage account.
    ws.paper_broker.reset_account(user.starting_cash, clear_history=clear_history)
    ws.refresh_risk_limits()
    snap = ws.snapshot_state()
    return {
        "account": snap["account"],
        "risk": snap["risk"],
    }


# ---- broker mode (paper <-> live Schwab) --------------------------------


@router.get("/api/broker")
def broker_state(request: Request, user: User = Depends(require_user)) -> dict:
    ws = _ws(user, request)
    return {
        "account_mode": ws.user.account_mode,
        "schwab": ws.schwab_status(),
        "live_risk_pct_per_trade": ws.user.live_risk_pct_per_trade,
        "live_max_portfolio_risk_pct": ws.user.live_max_portfolio_risk_pct,
        "paper_summary": ws.paper_broker.summary(),
        "live_summary": ws.live_broker.summary() if ws.live_broker is not None else None,
    }


@router.post("/api/broker/mode")
def set_broker_mode(
    req: BrokerModeRequest, request: Request, user: User = Depends(require_user),
) -> dict:
    ws = _ws(user, request)
    try:
        ws.set_account_mode(req.account_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"account_mode": ws.user.account_mode}


@router.patch("/api/broker/live-risk")
def update_live_risk(
    req: LiveRiskSettingsRequest, request: Request, user: User = Depends(require_user),
) -> dict:
    ws = _ws(user, request)
    try:
        ws.update_live_risk_settings(
            risk_pct_per_trade=req.risk_pct_per_trade,
            max_portfolio_risk_pct=req.max_portfolio_risk_pct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "live_risk_pct_per_trade": ws.user.live_risk_pct_per_trade,
        "live_max_portfolio_risk_pct": ws.user.live_max_portfolio_risk_pct,
    }


# ---- unified scanner (signals + AI brain behind one switch) ------------


@router.get("/api/scanner")
def scanner_state(request: Request, user: User = Depends(require_user)) -> dict:
    return _ws(user, request).scanner_snapshot()


@router.post("/api/scanner/toggle")
def toggle_scanner(request: Request, user: User = Depends(require_user)) -> dict:
    ws = _ws(user, request)
    ws.set_scanning(not ws.scanning_enabled)
    return {"enabled": ws.scanning_enabled}


@router.post("/api/scanner/run")
def run_scanner_now(request: Request, user: User = Depends(require_user)) -> dict:
    return _ws(user, request).scan_now()


@router.put("/api/scanner/watchlist")
def update_scanner_watchlist(
    req: WatchlistRequest, request: Request, user: User = Depends(require_user),
) -> dict:
    try:
        _ws(user, request).free_signals.set_watchlist(req.tickers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"watchlist": _ws(user, request).free_signals.config.watchlist}


# ---- legacy per-engine routes (kept for compatibility) -----------------


@router.get("/api/signals")
def signals_state(request: Request, user: User = Depends(require_user)) -> dict:
    return _ws(user, request).free_signals.snapshot()


@router.post("/api/signals/toggle")
def toggle_signals(request: Request, user: User = Depends(require_user)) -> dict:
    engine = _ws(user, request).free_signals
    engine.set_enabled(not engine.config.enabled)
    return {"enabled": engine.config.enabled}


@router.put("/api/signals/watchlist")
def update_watchlist(req: WatchlistRequest, request: Request, user: User = Depends(require_user)) -> dict:
    try:
        _ws(user, request).free_signals.set_watchlist(req.tickers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"watchlist": _ws(user, request).free_signals.config.watchlist}


@router.post("/api/signals/scan")
def scan_signals_now(request: Request, user: User = Depends(require_user)) -> dict:
    return _ws(user, request).free_signals.scan_now()


@router.get("/account")
def account(request: Request, user: User = Depends(require_user)) -> dict:
    return _ws(user, request).broker.summary()


@router.get("/positions")
def positions(request: Request, user: User = Depends(require_user), status: str | None = None) -> list[dict]:
    return [asdict(p) for p in _ws(user, request).broker.positions(status)]


@router.get("/journal")
def journal(request: Request, user: User = Depends(require_user), limit: int = 50) -> list[dict]:
    return _ws(user, request).broker._state["journal"][-limit:]


@router.post("/positions/check")
def check_positions(request: Request, user: User = Depends(require_user)) -> dict:
    closed = _ws(user, request).check_positions()
    return {
        "closed": [
            {"id": p.id, "underlying": p.underlying, "pnl": p.realized_pnl, "reason": p.exit_reason}
            for p in closed
        ]
    }


@router.post("/positions/{position_id}/close")
def close_position(position_id: str, request: Request, user: User = Depends(require_user)) -> dict:
    ws = _ws(user, request)
    pos = ws.broker.get_position(position_id)
    if pos is None or pos.status != "open":
        raise HTTPException(status_code=404, detail=f"no open position {position_id}")
    pipeline = ws.get_pipeline("day")
    snapshot = pipeline._snapshot_for_positions(pos.underlying, [pos])
    net = ws.broker.mark_position(pos, snapshot)
    if net is None:
        raise HTTPException(status_code=409, detail="no quotes available to price the close")
    closed = ws.broker.close_position(pos.id, net, reason="manual")
    return {"id": closed.id, "pnl": closed.realized_pnl, "exit_net": closed.exit_net}


def _run_alert_for_workspace(ws: UserWorkspace, alert: TradingViewAlert) -> None:
    ws.handle_alert(alert)


def resolve_webhook_workspace(secret: str):
    """Return workspace for a webhook secret, or None."""
    user = get_user_by_webhook_secret(secret)
    if user:
        return get_workspace_manager().get(user)

    expected = os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", "")
    if expected and hmac.compare_digest(secret or "", expected):
        from optionsagents.webapp.legacy import get_legacy_workspace
        return get_legacy_workspace()
    return None


@router.post("/webhook/tradingview")
async def tradingview_webhook(payload: dict, background: BackgroundTasks) -> dict:
    try:
        alert = TradingViewAlert.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    ws = resolve_webhook_workspace(alert.secret)
    if ws is None:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    background.add_task(_run_alert_for_workspace, ws, alert)
    return {
        "accepted": True,
        "ticker": alert.ticker,
        "signal": alert.signal,
        "mode": alert.mode,
        "detail": "processing in background",
    }
