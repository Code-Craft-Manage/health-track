"""Unit tests for ``healthcheck`` — the container liveness probe.

The bot rewrites a heartbeat file every ~30s from its asyncio loop; this probe
passes only while that file is fresh. The logic is pure stdlib (no telegram,
config, or network), so unlike the ``bot`` tests it needs no module stubbing —
just put the repo root on ``sys.path`` and import. Run with ``python3 -m unittest``.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import healthcheck  # noqa: E402


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self._now = 1_000_000.0

    def _heartbeat(self, contents: str):
        path = os.path.join(self.tmpdir(), "heartbeat")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(contents)
        from pathlib import Path

        return Path(path)

    def tmpdir(self):
        if not hasattr(self, "_dir"):
            import tempfile

            self._dir = tempfile.mkdtemp()
        return self._dir

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
        from pathlib import Path

        missing = Path(self.tmpdir()) / "does-not-exist"
        self.assertFalse(healthcheck.is_healthy(self._now, missing, max_age=120))

    def test_corrupt_heartbeat_is_unhealthy(self):
        hb = self._heartbeat("not-a-timestamp")
        self.assertFalse(healthcheck.is_healthy(self._now, hb, max_age=120))

    def test_age_is_none_when_unreadable(self):
        from pathlib import Path

        missing = Path(self.tmpdir()) / "nope"
        self.assertIsNone(healthcheck.heartbeat_age(self._now, missing))

    def test_age_is_seconds_since_write(self):
        hb = self._heartbeat(str(self._now - 42))
        self.assertAlmostEqual(healthcheck.heartbeat_age(self._now, hb), 42.0)


if __name__ == "__main__":
    unittest.main()
