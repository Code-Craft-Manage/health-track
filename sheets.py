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
    ("height", "Height (cm)"),
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


def _last_from_rows(rows: list[list[str]]) -> dict[str, tuple[str, str]]:
    """Most recent logged value (and the date it was logged) per measurement field.

    ``rows`` is the worksheet's full grid of strings, header row at index 0, laid
    out in canonical ``FIELDS`` order. Each check-in appends a dated row with
    blanks for fields not logged that time, so a field's latest value is the last
    non-blank cell in its column — and may come from a different row (and date)
    than another field's. Returns ``{field_key: (value, date)}``; fields that were
    never logged are simply absent, and an empty/header-only sheet yields ``{}``.
    """
    result: dict[str, tuple[str, str]] = {}
    if len(rows) < 2:
        return result
    body = rows[1:]
    for col, (key, _header) in enumerate(FIELDS):
        if key == "date":
            continue
        for row in reversed(body):
            cell = row[col].strip() if col < len(row) else ""
            if cell:
                date = row[0].strip() if row else ""  # column 0 is always the date
                result[key] = (cell, date)
                break
    return result


def last_values(tab: str) -> dict[str, tuple[str, str]]:
    """Read ``tab`` once and return the last logged value+date for each field."""
    worksheet = _get_worksheet(tab)
    return _last_from_rows(worksheet.get_all_values())


def append_measurements(data: dict, tab: str) -> None:
    """Append one measurement row to ``tab``, built in canonical ``FIELDS`` order.

    ``data`` maps field keys (including ``"date"``) to values. Fields that were
    not logged this time are written as blanks, so a weight-only entry leaves the
    other columns empty.
    """
    row = [data.get(key, "") for key, _ in FIELDS]
    worksheet = _get_worksheet(tab)
    worksheet.append_row(row, value_input_option="USER_ENTERED")
