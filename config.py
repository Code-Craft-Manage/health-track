"""Configuration and credential resolution for the Health-Track bot.

All settings are read from environment variables. For local development a
``.env`` file is loaded if present; in production the values are injected by the
deploy workflow (see ``.github/workflows/deploy.yml``) and are never written to
disk as a ``.env`` file.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load a local .env for development only. In production the variables are
# already present in the environment, so this is a harmless no-op.
load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# --- Telegram -------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")


def _parse_user_tabs(raw: str) -> dict[int, str]:
    """Parse the ``USER_TABS`` secret: a JSON object of Telegram-ID -> tab name.

    Example: ``{"000000000": "Alice", "111111111": "Bob"}``. This single
    secret is the source of truth for both *who is authorized* (its keys) and
    *which spreadsheet tab* each user logs to (its values). It is kept out of
    source control because it contains real user IDs and names.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"USER_TABS must be a JSON object of {{id: tab}}: {exc}"
        ) from exc
    if not isinstance(data, dict) or not data:
        raise RuntimeError("USER_TABS must be a non-empty JSON object of {id: tab}")
    tabs: dict[int, str] = {}
    for key, value in data.items():
        try:
            uid = int(key)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"USER_TABS key {key!r} is not a numeric ID") from exc
        if uid <= 0:
            # Telegram user IDs are positive; a non-positive ID can never match
            # a real user, so reject it rather than hide a misconfiguration.
            raise RuntimeError(f"USER_TABS key {key!r} is not a positive Telegram ID")
        if uid in tabs:
            # e.g. "1" and "01" both coerce to 1 — reject rather than silently
            # dropping one mapping.
            raise RuntimeError(f"USER_TABS has a duplicate user ID: {uid}")
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                f"USER_TABS tab name for ID {uid} must be a non-empty string"
            )
        tabs[uid] = value.strip()
    return tabs


# --- Google Sheets --------------------------------------------------------
GOOGLE_SHEET_ID: str = _require("GOOGLE_SHEET_ID")

# Per-user routing: each authorized user logs to their OWN tab in the same
# spreadsheet. To add someone: add their Telegram ID + tab name to the
# USER_TABS secret. Its keys double as the authorized-user allowlist.
USER_TABS: dict[int, str] = _parse_user_tabs(_require("USER_TABS"))

# Only users present in USER_TABS may use the bot.
AUTHORIZED_USER_IDS: set[int] = set(USER_TABS)


def tab_for_user(user_id: int) -> str | None:
    """Return the spreadsheet tab this Telegram user logs to (or None)."""
    return USER_TABS.get(user_id)

# --- Misc -----------------------------------------------------------------
# Timezone for the date stamp and the weekly reminder.
TIMEZONE: str = os.getenv("TZ", "America/Sao_Paulo")

# Local fallback used only when GOOGLE_OAUTH_TOKEN_B64 is not set (dev mode).
TOKEN_FILE = Path(__file__).resolve().parent / "token.json"


def get_oauth_token_json() -> str:
    """Return the Google OAuth user token as a JSON string.

    Production: decode the base64-encoded ``GOOGLE_OAUTH_TOKEN_B64`` secret.
    Local dev: read ``token.json`` from the project directory.
    """
    b64 = os.getenv("GOOGLE_OAUTH_TOKEN_B64")
    if b64:
        try:
            return base64.b64decode(b64).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"GOOGLE_OAUTH_TOKEN_B64 is not valid base64: {exc}"
            ) from exc

    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8")

    raise RuntimeError(
        "No Google OAuth token found. Set GOOGLE_OAUTH_TOKEN_B64, or place "
        "token.json in the project directory (run generate_token.py to create it)."
    )
