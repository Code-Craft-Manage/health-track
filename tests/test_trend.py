"""Unit tests for ``bot._trend`` — the directional arrow shown in the check-in
summary, comparing each new value to the user's last logged value.

``bot`` imports telegram/config/prefs/sheets at module load, none of which are
available outside the Docker image. The function under test touches none of
that, so we inject lightweight stubs into ``sys.modules`` (and dummy env vars)
*before* importing, mirroring ``test_last_values.py`` so this runs anywhere with
just the standard library:  ``python3 -m unittest``.
"""
import os
import sys
import types
import unittest

# --- Make ``import bot`` succeed without the runtime dependencies. -----------
_telegram = types.ModuleType("telegram")
for _attr in ("InlineKeyboardButton", "InlineKeyboardMarkup", "InputMediaPhoto", "Update"):
    setattr(_telegram, _attr, object)
sys.modules.setdefault("telegram", _telegram)

_tg_ext = types.ModuleType("telegram.ext")
for _attr in (
    "ApplicationBuilder",
    "CallbackQueryHandler",
    "CommandHandler",
    "ContextTypes",
    "ConversationHandler",
    "MessageHandler",
    "filters",
):
    setattr(_tg_ext, _attr, object)
sys.modules.setdefault("telegram.ext", _tg_ext)

_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", _dotenv)

# bot imports config, prefs, sheets — stub them so import succeeds.
_config = types.ModuleType("config")
_config.TZ = "UTC"
_config.tab_for_user = lambda *a, **k: None
sys.modules.setdefault("config", _config)

_prefs = types.ModuleType("prefs")
sys.modules.setdefault("prefs", _prefs)

_sheets = types.ModuleType("sheets")
_sheets.FIELDS = []
_sheets.append_measurements = lambda *a, **k: None
_sheets.last_values = lambda *a, **k: {}
sys.modules.setdefault("sheets", _sheets)

os.environ["TELEGRAM_BOT_TOKEN"] = "test"
os.environ["USER_TABS"] = '{"1": "Test"}'
os.environ["GOOGLE_SHEET_ID"] = "test"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import _trend  # noqa: E402


class _Ctx:
    """Stand-in for telegram's context: only ``user_data`` is read by _trend."""

    def __init__(self, last):
        self.user_data = {"last": last} if last is not None else {}


class TrendTests(unittest.TestCase):
    def test_higher_than_last_is_up(self):
        ctx = _Ctx({"weight": ("80", "01/06/2026")})
        self.assertEqual(_trend("weight", 80.5, ctx), " ⬆️")

    def test_lower_than_last_is_down(self):
        ctx = _Ctx({"weight": ("80", "01/06/2026")})
        self.assertEqual(_trend("weight", 79.0, ctx), " ⬇️")

    def test_equal_to_last_is_blank(self):
        ctx = _Ctx({"weight": ("80", "01/06/2026")})
        self.assertEqual(_trend("weight", 80.0, ctx), "")

    def test_no_history_for_field_is_blank(self):
        ctx = _Ctx({"height": ("175", "01/06/2026")})
        self.assertEqual(_trend("weight", 80.0, ctx), "")

    def test_no_last_data_at_all_is_blank(self):
        self.assertEqual(_trend("weight", 80.0, _Ctx(None)), "")

    def test_unparseable_previous_value_is_blank(self):
        ctx = _Ctx({"weight": ("n/a", "01/06/2026")})
        self.assertEqual(_trend("weight", 80.0, ctx), "")

    def test_decimal_strings_compare_numerically_not_lexically(self):
        # "9" < "80" as strings but 9 < 80 numerically too; guard the float path.
        ctx = _Ctx({"weight": ("9", "01/06/2026")})
        self.assertEqual(_trend("weight", 80.0, ctx), " ⬆️")


if __name__ == "__main__":
    unittest.main()
