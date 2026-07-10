"""HTTP middleware (CORS for Lovable / separate frontends)."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def configure_cors(app: FastAPI) -> None:
    """Allow browser clients hosted elsewhere (e.g. Lovable preview) to call the API."""
    raw = os.environ.get("OPTIONS_CORS_ORIGINS", "").strip()
    if not raw:
        return

    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        return

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
