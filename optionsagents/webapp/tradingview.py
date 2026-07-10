"""TradingView setup helpers: Pine script generation and connection guide."""

from __future__ import annotations

from pathlib import Path

_PINE_DIR = Path(__file__).resolve().parents[2] / "pine"


def _load_pine(name: str) -> str:
    return (_PINE_DIR / name).read_text(encoding="utf-8")


def pine_script_for_user(secret: str, script: str = "day") -> str:
    """Return Pine source with the user's webhook secret substituted."""
    filename = "day_trade_signal.pine" if script == "day" else "swing_trade_signal.pine"
    content = _load_pine(filename)
    return content.replace("REPLACE_WITH_YOUR_SECRET", secret)


def tradingview_setup_steps(webhook_url: str) -> list[dict]:
    return [
        {
            "step": 0,
            "title": "Free built-in signals (recommended)",
            "body": (
                "You do not need TradingView to use this app. Enable Free Signals on the "
                "Signals tab — the server scans your watchlist with the same EMA/VWAP/RSI "
                "rules as the Pine scripts and runs paper trades automatically. No paid "
                "subscription required."
            ),
        },
        {
            "step": 1,
            "title": "TradingView account requirements (optional)",
            "body": (
                "Only if you prefer TradingView charts: use a paid plan (Essential or "
                "higher) and enable two-factor authentication. Webhook alerts are not "
                "available on the free TradingView plan."
            ),
        },
        {
            "step": 2,
            "title": "Sign in to TradingView",
            "body": (
                "Open tradingview.com and sign in with your TradingView account. "
                "Enter the same username below so this dashboard can show your "
                "connection status."
            ),
            "link": "https://www.tradingview.com/",
        },
        {
            "step": 3,
            "title": "Copy your webhook URL",
            "body": (
                "In TradingView alert settings, paste this HTTPS URL as the "
                "webhook destination. Your server must be reachable on the public "
                "internet (deploy to a cloud host or use a tunnel for testing)."
            ),
            "code": webhook_url,
        },
        {
            "step": 4,
            "title": "Add the Pine script",
            "body": (
                "Open the Pine Editor in TradingView, paste the day or swing script "
                "from this page (your personal secret is already embedded), and add "
                "it to your chart."
            ),
        },
        {
            "step": 5,
            "title": "Create an alert",
            "body": (
                'Create one alert with condition "Any alert() function call". '
                "Leave the message box empty — the script sends JSON automatically. "
                "Set the webhook URL from step 3."
            ),
        },
        {
            "step": 6,
            "title": "Confirm connection",
            "body": (
                "Fire a test alert or click Confirm below once alerts are configured. "
                "Incoming alerts will open paper options trades in your account."
            ),
        },
    ]
