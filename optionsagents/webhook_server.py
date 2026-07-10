"""Web server: auth, mobile dashboard, per-user trading, TradingView webhooks."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from optionsagents.webapp.api import router as api_router
from optionsagents.webapp.auth import list_users
from optionsagents.webapp.database import get_db

# Re-export for CLI backward compatibility
from optionsagents.webapp.legacy import (  # noqa: F401
    get_broker,
    get_engine,
    get_legacy_workspace,
    get_orchestrator,
    get_pipeline,
)
from optionsagents.webapp.workspaces import get_workspace_manager

logger = logging.getLogger(__name__)
_STATIC_DIR = Path(__file__).parent / "static"


def _read_html(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    get_db()
    mgr = get_workspace_manager()
    users = list_users()
    if users:
        mgr.start_all(users)
    else:
        get_engine().start()
        get_orchestrator().start()
    yield
    mgr.stop_all()
    if not users:
        get_orchestrator().stop()
        get_engine().stop()


app = FastAPI(
    title="Options AI Agent",
    description="Mobile-ready web app with user accounts, autonomous AI trading, "
                "and TradingView webhook integration.",
    lifespan=_lifespan,
)

app.include_router(api_router)

_assets = _STATIC_DIR / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "users": len(list_users())}


@app.get("/")
def root():
    return RedirectResponse(url="/app", status_code=302)


@app.get("/app", response_class=HTMLResponse)
def app_page():
    return _read_html("app.html")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _read_html("login.html")


@app.get("/signup", response_class=HTMLResponse)
def signup_page():
    return _read_html("signup.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(_STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(_STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/index.html")
def legacy_index():
    return RedirectResponse(url="/app", status_code=302)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("OPTIONS_WEBHOOK_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
