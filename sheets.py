"""Google Sheets integration: authorize with an OAuth user token and append rows.

The canonical column order of the sheet lives here in ``FIELDS`` and is the
single source of truth shared with ``bot.py`` (which builds its prompts from it),
so the conversation order and the spreadsheet columns can never drift apart.
"""
from __future__ import annotations

import json

import gspread
from google.oauth2.credentials import Credentials

import config

# We only need to read/write spreadsheet data.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Canonical column order: (field key, human header). "date" is filled by the
# bot; every other key is collected from the user. Row 1 of the sheet should
# contain these headers in this exact order.
FIELDS: list[tuple[str, str]] = [
    ("date", "Date (DD/MM/YYYY)"),
    ("weight", "Weight (kg)"),
    ("neck", "Neck (cm)"),
    ("shoulders", "Shoulders (cm)"),
    ("chest", "Chest (cm)"),
    ("biceps_left", "Biceps Left (cm)"),
    ("biceps_right", "Biceps Right (cm)"),
    ("waist", "Waist (cm)"),
    ("abdomen", "Abdomen (cm)"),
    ("hips", "Hips (cm)"),
    ("thigh_left", "Thigh Left (cm)"),
    ("thigh_right", "Thigh Right (cm)"),
    ("calf_left", "Calf Left (cm)"),
    ("calf_right", "Calf Right (cm)"),
]

HEADERS: list[str] = [header for _, header in FIELDS]


def _get_worksheet(tab: str) -> gspread.Worksheet:
    info = json.loads(config.get_oauth_token_json())
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    return spreadsheet.worksheet(tab)


def append_measurements(data: dict, tab: str) -> None:
    """Append one measurement row to ``tab``, built in canonical ``FIELDS`` order.

    ``data`` maps field keys (including ``"date"``) to values. Fields that were
    not logged this time are written as blanks, so a weight-only entry leaves the
    other columns empty.
    """
    row = [data.get(key, "") for key, _ in FIELDS]
    worksheet = _get_worksheet(tab)
    worksheet.append_row(row, value_input_option="USER_ENTERED")
