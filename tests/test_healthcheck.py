"""Unit tests for ``healthcheck`` — the container liveness probe.

The bot rewrites a heartbeat file every ~30s from its asyncio loop; this probe
passes only while that file is fresh. The logic is pure stdlib (no telegram,
config, or network), so unlike the ``bot`` tests it needs no module stubbing —
just put the repo root on ``sys.path`` and import. Run with ``python3 -m unittest``.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import healthcheck  # noqa: E402


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self._now = 1_000_000.0
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _heartbeat(self, contents: str) -> Path:
        path = Path(self._tmp.name) / "heartbeat"
        path.write_text(contents, encoding="utf-8")
        return path

    def _missing(self) -> Path:
        return Path(self._tmp.name) / "does-not-exist"

    def test_fresh_heartbeat_is_healthy(self):
        hb = self._heartbeat(str(self._now - 10))
        self.assertTrue(healthcheck.is_healthy(self._now, hb, max_age=120))

    def test_just_within_max_age_is_healthy(self):
        hb = self._heartbeat(str(self._now - 120))
        self.assertTrue(healthcheck.is_healthy(self._now, hb, max_age=120))

    def test_stale_heartbeat_is_unhealthy(self):
        hb = self._heartbeat(str(self._now - 300))
        self.assertFalse(healthcheck.is_healthy(self._now, hb, max_age=120))

    def test_missing_heartbeat_is_unhealthy(self):
        self.assertFalse(healthcheck.is_healthy(self._now, self._missing(), max_age=120))

    def test_corrupt_heartbeat_is_unhealthy(self):
        hb = self._heartbeat("not-a-timestamp")
        self.assertFalse(healthcheck.is_healthy(self._now, hb, max_age=120))

    def test_age_is_none_when_unreadable(self):
        self.assertIsNone(healthcheck.heartbeat_age(self._now, self._missing()))

    def test_age_is_seconds_since_write(self):
        hb = self._heartbeat(str(self._now - 42))
        self.assertAlmostEqual(healthcheck.heartbeat_age(self._now, hb), 42.0)


if __name__ == "__main__":
    unittest.main()
