"""Web application layer: user accounts, sessions, and per-user trading workspaces."""

from optionsagents.webapp.auth import User, require_user
from optionsagents.webapp.database import get_db
from optionsagents.webapp.workspaces import WorkspaceManager

__all__ = ["User", "WorkspaceManager", "get_db", "require_user"]
