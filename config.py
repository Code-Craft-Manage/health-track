"""Configuration and credential resolution for the Health-Track bot.

All settings are read from environment variables. For local development a
``.env`` file is loaded if present; in production the values are injected by the
deploy workflow (see ``.github/workflows/deploy.yml``) and are never written to
disk as a ``.env`` file.
"""
from __future__ import annotations

import base64
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
AUTHORIZED_USER_ID: int = int(_require("AUTHORIZED_USER_ID"))

# --- Google Sheets --------------------------------------------------------
GOOGLE_SHEET_ID: str = _require("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB: str = os.getenv("GOOGLE_SHEET_TAB", "Measurements")

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
