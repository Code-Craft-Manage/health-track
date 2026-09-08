"""Tests for USER_TABS parsing in config.py (pure logic, no network)."""
import os
import sys
import types
import unittest

# Stub python-dotenv so importing config doesn't require the package.
_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", _dotenv)

# Minimal env so config's module-level _require() calls succeed on import.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("USER_TABS", '{"1": "Test"}')
os.environ.setdefault("GOOGLE_SHEET_ID", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


class ParseUserTabsTest(unittest.TestCase):
    def test_valid_mapping(self):
        tabs = config._parse_user_tabs('{"111111111": "Alice", "222222222": "Bob"}')
        self.assertEqual(tabs, {111111111: "Alice", 222222222: "Bob"})

    def test_keys_are_the_allowlist(self):
        # The keys double as the authorized-user allowlist.
        tabs = config._parse_user_tabs('{"1": "A", "2": "B"}')
        self.assertEqual(set(tabs), {1, 2})

    def test_tab_name_is_trimmed(self):
        self.assertEqual(config._parse_user_tabs('{"1": "  Alice  "}')[1], "Alice")

    def test_invalid_json_rejected(self):
        with self.assertRaises(RuntimeError):
            config._parse_user_tabs("not json")

    def test_empty_object_rejected(self):
        with self.assertRaises(RuntimeError):
            config._parse_user_tabs("{}")

    def test_non_object_rejected(self):
        with self.assertRaises(RuntimeError):
            config._parse_user_tabs('["a", "b"]')

    def test_non_numeric_key_rejected(self):
        with self.assertRaises(RuntimeError):
            config._parse_user_tabs('{"abc": "Alice"}')

    def test_non_positive_id_rejected(self):
        for raw in ('{"0": "Alice"}', '{"-1": "Alice"}'):
            with self.assertRaises(RuntimeError):
                config._parse_user_tabs(raw)

    def test_empty_tab_name_rejected(self):
        with self.assertRaises(RuntimeError):
            config._parse_user_tabs('{"1": "   "}')

    def test_null_tab_name_rejected(self):
        with self.assertRaises(RuntimeError):
            config._parse_user_tabs('{"1": null}')

    def test_duplicate_id_after_coercion_rejected(self):
        # "1" and "01" both coerce to the same int.
        with self.assertRaises(RuntimeError):
            config._parse_user_tabs('{"1": "A", "01": "B"}')


if __name__ == "__main__":
    unittest.main()
