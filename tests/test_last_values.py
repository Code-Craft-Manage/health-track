"""Unit tests for ``sheets._last_from_rows`` — the pure logic behind the
"last logged value" reminder shown when entering a new measurement.

``sheets`` imports gspread/google-auth at module load and ``config`` requires
env vars, neither of which is available outside the Docker image. Since the
function under test touches none of that, we inject lightweight stubs into
``sys.modules`` and set dummy env vars *before* importing, so the test runs
anywhere with just the standard library:  ``python3 -m unittest``.
"""
import os
import sys
import types
import unittest

# --- Make ``import sheets`` succeed without the runtime dependencies. --------
for name in ("gspread",):
    sys.modules.setdefault(name, types.ModuleType(name))

_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", _dotenv)

_google = types.ModuleType("google")
_oauth2 = types.ModuleType("google.oauth2")
_creds = types.ModuleType("google.oauth2.credentials")
_creds.Credentials = object
_oauth2.credentials = _creds
_google.oauth2 = _oauth2
sys.modules.setdefault("google", _google)
sys.modules.setdefault("google.oauth2", _oauth2)
sys.modules.setdefault("google.oauth2.credentials", _creds)

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("AUTHORIZED_USER_ID", "1")
os.environ.setdefault("GOOGLE_SHEET_ID", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sheets import FIELDS, _last_from_rows  # noqa: E402

HEADERS = [header for _, header in FIELDS]


def _row(date="", **values):
    """Build a sheet row in canonical FIELDS order from keyword field values."""
    row = ["" for _ in FIELDS]
    row[0] = date
    for key, val in values.items():
        idx = next(i for i, (k, _) in enumerate(FIELDS) if k == key)
        row[idx] = val
    return row


class LastFromRowsTests(unittest.TestCase):
    def test_empty_sheet_returns_empty(self):
        self.assertEqual(_last_from_rows([]), {})

    def test_header_only_returns_empty(self):
        self.assertEqual(_last_from_rows([HEADERS]), {})

    def test_picks_last_non_blank_per_field(self):
        rows = [
            HEADERS,
            _row(date="01/06/2026", weight="80", height="175"),
            _row(date="08/06/2026", weight="79.5"),
        ]
        result = _last_from_rows(rows)
        # Weight's latest is the newest row; height's latest is the older row
        # (it was blank in the newest), each carrying its own date.
        self.assertEqual(result["weight"], ("79.5", "08/06/2026"))
        self.assertEqual(result["height"], ("175", "01/06/2026"))

    def test_never_logged_field_is_absent(self):
        rows = [HEADERS, _row(date="01/06/2026", weight="80")]
        result = _last_from_rows(rows)
        self.assertIn("weight", result)
        self.assertNotIn("neck", result)

    def test_blanks_and_whitespace_are_skipped(self):
        rows = [
            HEADERS,
            _row(date="01/06/2026", neck="38"),
            _row(date="08/06/2026", neck="   "),  # whitespace-only counts as blank
        ]
        self.assertEqual(_last_from_rows(rows)["neck"], ("38", "01/06/2026"))

    def test_date_never_included_as_a_field(self):
        rows = [HEADERS, _row(date="01/06/2026", weight="80")]
        self.assertNotIn("date", _last_from_rows(rows))

    def test_short_trailing_rows_do_not_raise(self):
        # Google can return ragged rows truncated at the last non-empty cell.
        rows = [HEADERS, ["08/06/2026", "79"]]  # only date + weight present
        result = _last_from_rows(rows)
        self.assertEqual(result["weight"], ("79", "08/06/2026"))
        self.assertNotIn("neck", result)


if __name__ == "__main__":
    unittest.main()
