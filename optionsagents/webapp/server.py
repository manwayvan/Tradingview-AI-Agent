"""Web server entrypoint."""

from optionsagents.webapp.legacy import (
    get_broker,
    get_engine,
    get_legacy_workspace,
    get_orchestrator,
    get_pipeline,
)
from optionsagents.webhook_server import app, main

__all__ = [
    "app",
    "main",
    "get_broker",
    "get_engine",
    "get_orchestrator",
    "get_pipeline",
    "get_legacy_workspace",
]
